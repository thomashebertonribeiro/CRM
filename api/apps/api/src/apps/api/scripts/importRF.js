/**
 * Receita Federal CNPJ Import Script
 *
 * Downloads and imports the public CNPJ data from Receita Federal into SQLite.
 * Supports zip files with any internal file extension (.ESTABELE, .MUNICCSV, etc.)
 *
 * Usage:
 *   node src/apps/api/scripts/importRF.js
 *
 * Options (env vars):
 *   RF_DATA_DIR   - directory to store data (default: ./data/rf)
 *   RF_UF_FILTER  - comma-separated UFs to import (e.g. "PR,SC,RS")
 *   RF_FILES      - comma-separated file indices (e.g. "0,1,2") — all 10 if not set
 */

import Database from 'better-sqlite3';
import AdmZip from 'adm-zip';
import fs from 'fs';
import path from 'path';
import https from 'https';
import http from 'http';

const DATA_DIR = process.env.RF_DATA_DIR || './data/rf';
const DB_PATH = path.join(DATA_DIR, 'cnpj.db');
const RF_BASE_URL = process.env.RF_BASE_URL || 'https://dadosabertos.rfb.gov.br/CNPJ';
const RF_MIRROR_URL = 'https://github.com/jonathands/dados-abertos-receita-cnpj/releases/download/2023.05';

const UF_FILTER = process.env.RF_UF_FILTER
  ? process.env.RF_UF_FILTER.toUpperCase().split(',').map(s => s.trim())
  : null;

const FILE_INDICES = process.env.RF_FILES
  ? process.env.RF_FILES.split(',').map(s => parseInt(s.trim(), 10))
  : [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];

function log(msg) {
  console.log(`[${new Date().toISOString()}] ${msg}`);
}

function downloadFile(url, destPath) {
  return new Promise((resolve, reject) => {
    log(`Downloading: ${url}`);
    const file = fs.createWriteStream(destPath);

    function tryDownload(targetUrl, redirectCount = 0) {
      if (redirectCount > 10) return reject(new Error('Too many redirects'));
      const lib = targetUrl.startsWith('https') ? https : http;
      const req = lib.get(targetUrl, { timeout: 60000 }, (res) => {
        if (res.statusCode === 301 || res.statusCode === 302 || res.statusCode === 307 || res.statusCode === 308) {
          return tryDownload(res.headers.location, redirectCount + 1);
        }
        if (res.statusCode !== 200) {
          file.close();
          if (fs.existsSync(destPath)) fs.unlinkSync(destPath);
          return reject(new Error(`HTTP ${res.statusCode} for ${targetUrl}`));
        }
        const total = parseInt(res.headers['content-length'] || '0', 10);
        let downloaded = 0;
        let lastPct = -1;
        res.on('data', (chunk) => {
          downloaded += chunk.length;
          if (total > 0) {
            const pct = Math.floor(downloaded / total * 100);
            if (pct !== lastPct && pct % 10 === 0) {
              log(`  ${pct}% (${(downloaded / 1024 / 1024).toFixed(1)}MB / ${(total / 1024 / 1024).toFixed(1)}MB)`);
              lastPct = pct;
            }
          }
        });
        res.pipe(file);
        file.on('finish', () => { file.close(); resolve(); });
      });
      req.on('error', (err) => {
        file.close();
        if (fs.existsSync(destPath)) fs.unlinkSync(destPath);
        reject(err);
      });
    }

    tryDownload(url);
  });
}

async function downloadWithFallback(filename, destPath) {
  try {
    await downloadFile(`${RF_BASE_URL}/${filename}`, destPath);
    return;
  } catch (e) {
    log(`Official RF server failed (${e.message}), trying GitHub mirror...`);
    if (fs.existsSync(destPath)) fs.unlinkSync(destPath);
  }
  await downloadFile(`${RF_MIRROR_URL}/${filename}`, destPath);
}

function setupDatabase(db) {
  db.pragma('journal_mode = WAL');
  db.pragma('synchronous = NORMAL');
  db.pragma('cache_size = -128000');
  db.pragma('temp_store = MEMORY');

  db.exec(`
    CREATE TABLE IF NOT EXISTS municipios (
      codigo TEXT PRIMARY KEY,
      nome TEXT NOT NULL,
      nome_normalizado TEXT NOT NULL,
      uf TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS estabelecimentos (
      cnpj_basico TEXT NOT NULL,
      cnpj_ordem TEXT NOT NULL,
      cnpj_dv TEXT NOT NULL,
      identificador_matriz_filial TEXT,
      nome_fantasia TEXT,
      situacao_cadastral TEXT,
      data_situacao_cadastral TEXT,
      motivo_situacao_cadastral TEXT,
      nome_cidade_exterior TEXT,
      pais TEXT,
      data_inicio_atividade TEXT,
      cnae_fiscal_principal TEXT,
      cnae_fiscal_secundaria TEXT,
      tipo_logradouro TEXT,
      logradouro TEXT,
      numero TEXT,
      complemento TEXT,
      bairro TEXT,
      cep TEXT,
      uf TEXT,
      municipio TEXT,
      municipio_nome TEXT,
      ddd_1 TEXT,
      telefone_1 TEXT,
      ddd_2 TEXT,
      telefone_2 TEXT,
      ddd_fax TEXT,
      fax TEXT,
      email TEXT,
      situacao_especial TEXT,
      data_situacao_especial TEXT,
      PRIMARY KEY (cnpj_basico, cnpj_ordem, cnpj_dv)
    );

    CREATE TABLE IF NOT EXISTS meta (
      key TEXT PRIMARY KEY,
      value TEXT
    );
  `);

  db.exec(`
    CREATE INDEX IF NOT EXISTS idx_cnae ON estabelecimentos(cnae_fiscal_principal);
    CREATE INDEX IF NOT EXISTS idx_municipio ON estabelecimentos(municipio);
    CREATE INDEX IF NOT EXISTS idx_cnae_municipio ON estabelecimentos(cnae_fiscal_principal, municipio);
    CREATE INDEX IF NOT EXISTS idx_situacao ON estabelecimentos(situacao_cadastral);
    CREATE INDEX IF NOT EXISTS idx_uf ON estabelecimentos(uf);
  `);
}

function normalizeText(str) {
  if (!str) return '';
  return str.toUpperCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim();
}

/**
 * Get the first non-directory entry from a zip file.
 * RF files use non-standard extensions like .MUNICCSV, .ESTABELE
 */
function getFirstEntry(zipPath) {
  const zip = new AdmZip(zipPath);
  const entries = zip.getEntries();
  log(`  ZIP entries: ${entries.map(e => `${e.entryName}(${e.getData().length}b)`).join(', ')}`);
  return entries.find(e => !e.isDirectory && e.getData().length > 100) || entries[0];
}

async function importMunicipios(db, zipPath) {
  log('Importing municipios...');

  const entry = getFirstEntry(zipPath);
  if (!entry) {
    log('WARNING: No entry found in Municipios.zip, skipping');
    return;
  }

  log(`  Reading entry: ${entry.entryName}`);
  const content = entry.getData().toString('latin1');
  const lines = content.split('\n');

  const insert = db.prepare(
    'INSERT OR REPLACE INTO municipios (codigo, nome, nome_normalizado, uf) VALUES (?, ?, ?, ?)'
  );
  const insertMany = db.transaction((rows) => {
    for (const row of rows) insert.run(...row);
  });

  const rows = [];
  for (const line of lines) {
    const parts = line.split(';');
    if (parts.length < 2) continue;
    const codigo = parts[0]?.trim().replace(/"/g, '');
    const nome = parts[1]?.trim().replace(/"/g, '');
    const uf = parts[2]?.trim().replace(/"/g, '') || '';
    if (!codigo || !nome) continue;
    rows.push([codigo, nome, normalizeText(nome), uf]);
  }

  insertMany(rows);
  log(`Imported ${rows.length} municipios`);
}

async function importEstabelecimentos(db, zipPath, fileIndex) {
  log(`Importing Estabelecimentos${fileIndex}...`);

  // Check file size first
  const stats = fs.statSync(zipPath);
  log(`  ZIP size: ${(stats.size / 1024 / 1024).toFixed(1)}MB`);

  // Build municipio lookup map
  const municipioMap = new Map();
  const municipioRows = db.prepare('SELECT codigo, nome FROM municipios').all();
  for (const row of municipioRows) municipioMap.set(row.codigo, row.nome);
  log(`  Loaded ${municipioMap.size} municipios for lookup`);

  // Use streaming unzip to avoid loading 500MB into memory
  // Extract to temp file first, then process line by line
  const tmpCsv = path.join(DATA_DIR, `_tmp_estab${fileIndex}.csv`);

  try {
    // Extract using unzip command (available in alpine)
    log(`  Extracting ZIP...`);
    const { execSync } = await import('child_process');
    execSync(`unzip -p "${zipPath}" > "${tmpCsv}"`, { maxBuffer: 1024 * 1024 * 10 });
    log(`  Extracted to temp file`);
  } catch (e) {
    // Fallback: try adm-zip with streaming
    log(`  unzip failed (${e.message}), trying adm-zip...`);
    try {
      const zip = new AdmZip(zipPath);
      const entries = zip.getEntries();
      log(`  ZIP entries: ${entries.map(en => `${en.entryName}(${en.header.size}b)`).join(', ')}`);
      const entry = entries.find(en => !en.isDirectory);
      if (!entry) {
        log(`  No entry found, skipping`);
        return 0;
      }
      log(`  Extracting entry: ${entry.entryName}`);
      zip.extractEntryTo(entry, DATA_DIR, false, true, false, `_tmp_estab${fileIndex}.csv`);
    } catch (e2) {
      log(`  adm-zip also failed: ${e2.message}`);
      return 0;
    }
  }

  if (!fs.existsSync(tmpCsv)) {
    log(`  Temp file not created, skipping`);
    return 0;
  }

  const tmpStats = fs.statSync(tmpCsv);
  log(`  Temp CSV size: ${(tmpStats.size / 1024 / 1024).toFixed(1)}MB`);

  // Process line by line using readline (streaming, low memory)
  const { createInterface } = await import('readline');
  const rl = createInterface({
    input: fs.createReadStream(tmpCsv, { encoding: 'latin1' }),
    crlfDelay: Infinity,
  });

  const insert = db.prepare(`
    INSERT OR REPLACE INTO estabelecimentos VALUES (
      ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
    )
  `);
  const insertMany = db.transaction((rows) => {
    for (const row of rows) insert.run(...row);
  });

  let count = 0;
  let batch = [];
  const BATCH_SIZE = 5000;

  for await (const line of rl) {
    if (!line.trim()) continue;
    const parts = line.split(';').map(p => p.trim().replace(/^"|"$/g, '') || null);
    if (parts.length < 20) continue;

    const uf = parts[19] || '';
    if (UF_FILTER && !UF_FILTER.includes(uf)) continue;

    const municipioCodigo = parts[20] || '';
    const municipioNome = municipioMap.get(municipioCodigo) || '';

    batch.push([
      parts[0], parts[1], parts[2], parts[3], parts[4],
      parts[5], parts[6], parts[7], parts[8], parts[9],
      parts[10], parts[11], parts[12], parts[13], parts[14],
      parts[15], parts[16], parts[17], parts[18], uf,
      municipioCodigo, municipioNome,
      parts[21], parts[22], parts[23], parts[24], parts[25],
      parts[26], parts[27], parts[28], parts[29],
    ]);

    if (batch.length >= BATCH_SIZE) {
      insertMany(batch);
      count += batch.length;
      batch = [];
      if (count % 50000 === 0) log(`  ${count.toLocaleString()} records imported...`);
    }
  }

  if (batch.length > 0) {
    insertMany(batch);
    count += batch.length;
  }

  // Cleanup temp file
  try { fs.unlinkSync(tmpCsv); } catch (_) {}

  log(`  File ${fileIndex}: ${count.toLocaleString()} records imported`);
  return count;
}

// ─── Main ───

async function main() {
  log('=== Receita Federal CNPJ Import ===');
  if (UF_FILTER) log(`UF filter: ${UF_FILTER.join(', ')}`);
  log(`Files to import: ${FILE_INDICES.join(', ')}`);

  fs.mkdirSync(DATA_DIR, { recursive: true });
  const db = new Database(DB_PATH);
  setupDatabase(db);

  // 1. Municipios
  const municipiosZip = path.join(DATA_DIR, 'Municipios.zip');
  if (!fs.existsSync(municipiosZip)) {
    await downloadWithFallback('Municipios.zip', municipiosZip);
  } else {
    log('Municipios.zip already downloaded, skipping...');
  }
  await importMunicipios(db, municipiosZip);

  // 2. Estabelecimentos
  let totalImported = 0;
  for (const i of FILE_INDICES) {
    const zipName = `Estabelecimentos${i}.zip`;
    const zipPath = path.join(DATA_DIR, zipName);

    if (!fs.existsSync(zipPath)) {
      await downloadWithFallback(zipName, zipPath);
    } else {
      log(`${zipName} already downloaded, skipping download...`);
    }

    const count = await importEstabelecimentos(db, zipPath, i);
    totalImported += count;

    if (process.env.RF_DELETE_ZIPS === 'true') {
      fs.unlinkSync(zipPath);
      log(`Deleted ${zipName} to save space`);
    }
  }

  // 3. Metadata
  db.prepare("INSERT OR REPLACE INTO meta VALUES ('imported_at', ?)").run(new Date().toISOString());
  db.prepare("INSERT OR REPLACE INTO meta VALUES ('uf_filter', ?)").run(UF_FILTER ? UF_FILTER.join(',') : 'ALL');
  db.prepare("INSERT OR REPLACE INTO meta VALUES ('total_records', ?)").run(String(totalImported));

  // 4. Stats
  const stats = db.prepare('SELECT COUNT(*) as cnt FROM estabelecimentos').get();
  const ativos = db.prepare("SELECT COUNT(*) as cnt FROM estabelecimentos WHERE situacao_cadastral = '02'").get();

  log('=== Import Complete ===');
  log(`Total records: ${stats.cnt.toLocaleString()}`);
  log(`Active companies: ${ativos.cnt.toLocaleString()}`);
  log(`Database: ${DB_PATH}`);

  db.close();
}

main().catch(err => {
  console.error('Import failed:', err);
  process.exit(1);
});
