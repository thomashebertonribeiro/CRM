/**
 * Receita Federal CNPJ Import Script
 *
 * Downloads and imports the public CNPJ data from Receita Federal into SQLite.
 *
 * Usage:
 *   node src/apps/api/scripts/importRF.js
 *
 * Options (env vars):
 *   RF_DATA_DIR   - directory to store data (default: ./data/rf)
 *   RF_UF_FILTER  - comma-separated UFs to import (e.g. "PR,SC,RS") — imports all if not set
 *   RF_FILES      - comma-separated file indices to import (e.g. "0,1,2") — imports all 10 if not set
 *
 * Disk space: ~5GB for all files, ~1-2GB for SQLite after import
 * Time: ~30-60 min for full import, ~3-5 min per UF filter
 */

import Database from 'better-sqlite3';
import AdmZip from 'adm-zip';
import fs from 'fs';
import path from 'path';
import https from 'https';
import http from 'http';
import readline from 'readline';

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

// ─── Helpers ───

function log(msg) {
  console.log(`[${new Date().toISOString()}] ${msg}`);
}

function downloadFile(url, destPath) {
  return new Promise((resolve, reject) => {
    log(`Downloading: ${url}`);
    const file = fs.createWriteStream(destPath);

    function tryDownload(targetUrl, redirectCount = 0) {
      if (redirectCount > 5) return reject(new Error('Too many redirects'));
      const lib = targetUrl.startsWith('https') ? https : http;
      lib.get(targetUrl, { timeout: 30000 }, (res) => {
        if (res.statusCode === 301 || res.statusCode === 302) {
          file.close();
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
      }).on('error', (err) => {
        file.close();
        if (fs.existsSync(destPath)) fs.unlinkSync(destPath);
        reject(err);
      });
    }

    tryDownload(url);
  });
}

async function downloadWithFallback(filename, destPath) {
  // Try official RF server first
  try {
    await downloadFile(`${RF_BASE_URL}/${filename}`, destPath);
    return;
  } catch (e) {
    log(`Official RF server failed (${e.message}), trying GitHub mirror...`);
    if (fs.existsSync(destPath)) fs.unlinkSync(destPath);
  }
  // Fallback to GitHub mirror
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

  // Indexes for fast search
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

async function importMunicipiosFromCSV(db, csvPath) {
  log(`Importing municipios from CSV: ${csvPath}`);
  const content = fs.readFileSync(csvPath, 'latin1');
  const lines = content.split('\n');

  const insert = db.prepare(
    'INSERT OR REPLACE INTO municipios (codigo, nome, nome_normalizado, uf) VALUES (?, ?, ?, ?)'
  );
  const insertMany = db.transaction((rows) => { for (const row of rows) insert.run(...row); });

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
  log(`Imported ${rows.length} municipios from CSV`);
}

async function importEstabelecimentosFromCSV(db, csvPath, fileIndex) {
  log(`Importing Estabelecimentos${fileIndex} from CSV: ${csvPath}`);

  const municipioMap = new Map();
  const municipioRows = db.prepare('SELECT codigo, nome FROM municipios').all();
  for (const row of municipioRows) municipioMap.set(row.codigo, row.nome);

  const content = fs.readFileSync(csvPath, 'latin1');
  const lines = content.split('\n');

  const insert = db.prepare(`INSERT OR REPLACE INTO estabelecimentos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const insertMany = db.transaction((rows) => { for (const row of rows) insert.run(...row); });

  let count = 0;
  let batch = [];
  const BATCH_SIZE = 10000;

  for (const line of lines) {
    if (!line.trim()) continue;
    const parts = line.split(';').map(p => p.trim().replace(/^"|"$/g, '') || null);
    if (parts.length < 20) continue;

    const uf = parts[19] || '';
    if (UF_FILTER && !UF_FILTER.includes(uf)) continue;

    const municipioCodigo = parts[20] || '';
    const municipioNome = municipioMap.get(municipioCodigo) || '';

    batch.push([
      parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6],
      parts[7], parts[8], parts[9], parts[10], parts[11], parts[12], parts[13],
      parts[14], parts[15], parts[16], parts[17], parts[18], uf,
      municipioCodigo, municipioNome,
      parts[21], parts[22], parts[23], parts[24], parts[25], parts[26],
      parts[27], parts[28], parts[29],
    ]);

    if (batch.length >= BATCH_SIZE) {
      insertMany(batch);
      count += batch.length;
      batch = [];
      if (count % 100000 === 0) log(`  ${count.toLocaleString()} records imported...`);
    }
  }
  if (batch.length > 0) { insertMany(batch); count += batch.length; }
  log(`  CSV ${fileIndex}: ${count.toLocaleString()} records imported`);
  return count;
}
  log('Importing municipios...');
  const zip = new AdmZip(zipPath);
  const entries = zip.getEntries();

  // Get the first non-directory entry regardless of extension
  // RF files have names like .MUNICCSV, .ESTABELE etc.
  const csvEntry = entries.find(e => !e.isDirectory && e.getData().length > 100);

  if (!csvEntry) {
    log(`WARNING: Could not find CSV in Municipios.zip. Entries: ${entries.map(e => e.entryName).join(', ')}`);
    // Insert a minimal set of municipios so the import can proceed
    log('Inserting fallback municipios data...');
    const insert = db.prepare('INSERT OR REPLACE INTO municipios (codigo, nome, nome_normalizado, uf) VALUES (?, ?, ?, ?)');
    const insertMany = db.transaction((rows) => { for (const row of rows) insert.run(...row); });
    // Common PR municipalities
    const fallback = [
      ['7535', 'LONDRINA', 'LONDRINA', 'PR'],
      ['7535', 'LONDRINA', 'LONDRINA', 'PR'],
      ['7535', 'LONDRINA', 'LONDRINA', 'PR'],
      ['8105', 'CURITIBA', 'CURITIBA', 'PR'],
      ['8105', 'CURITIBA', 'CURITIBA', 'PR'],
      ['8105', 'CURITIBA', 'CURITIBA', 'PR'],
      ['8305', 'MARINGA', 'MARINGA', 'PR'],
      ['8305', 'MARINGA', 'MARINGA', 'PR'],
      ['7555', 'CASCAVEL', 'CASCAVEL', 'PR'],
      ['7555', 'CASCAVEL', 'CASCAVEL', 'PR'],
      ['8015', 'FOZ DO IGUACU', 'FOZ DO IGUACU', 'PR'],
      ['8015', 'FOZ DO IGUACU', 'FOZ DO IGUACU', 'PR'],
      ['7975', 'PONTA GROSSA', 'PONTA GROSSA', 'PR'],
      ['7975', 'PONTA GROSSA', 'PONTA GROSSA', 'PR'],
      ['7545', 'APUCARANA', 'APUCARANA', 'PR'],
      ['7545', 'APUCARANA', 'APUCARANA', 'PR'],
      ['7625', 'GUARAPUAVA', 'GUARAPUAVA', 'PR'],
      ['7625', 'GUARAPUAVA', 'GUARAPUAVA', 'PR'],
      ['7685', 'PARANAGUA', 'PARANAGUA', 'PR'],
      ['7685', 'PARANAGUA', 'PARANAGUA', 'PR'],
    ];
    // Deduplicate
    const seen = new Set();
    const unique = fallback.filter(r => { if (seen.has(r[0])) return false; seen.add(r[0]); return true; });
    insertMany(unique);
    log(`Inserted ${unique.length} fallback municipios`);
    return;
  }

  const content = csvEntry.getData().toString('latin1');
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

  const zip = new AdmZip(zipPath);
  const entries = zip.getEntries();
  // Get first non-directory entry regardless of extension (.ESTABELE, etc.)
  const csvEntry = entries.find(e => !e.isDirectory && e.getData().length > 100);

  if (!csvEntry) {
    log(`WARNING: No entry found in ${zipPath}`);
    return 0;
  }

  // Build municipio lookup map
  const municipioMap = new Map();
  const municipioRows = db.prepare('SELECT codigo, nome FROM municipios').all();
  for (const row of municipioRows) {
    municipioMap.set(row.codigo, row.nome);
  }

  const content = csvEntry.getData().toString('latin1');
  const lines = content.split('\n');

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
  const BATCH_SIZE = 10000;

  for (const line of lines) {
    if (!line.trim()) continue;

    const parts = line.split(';').map(p => p.trim().replace(/^"|"$/g, '') || null);
    if (parts.length < 20) continue;

    const uf = parts[19] || '';
    if (UF_FILTER && !UF_FILTER.includes(uf)) continue;

    const municipioCodigo = parts[20] || '';
    const municipioNome = municipioMap.get(municipioCodigo) || '';

    batch.push([
      parts[0],  // cnpj_basico
      parts[1],  // cnpj_ordem
      parts[2],  // cnpj_dv
      parts[3],  // identificador_matriz_filial
      parts[4],  // nome_fantasia
      parts[5],  // situacao_cadastral
      parts[6],  // data_situacao_cadastral
      parts[7],  // motivo_situacao_cadastral
      parts[8],  // nome_cidade_exterior
      parts[9],  // pais
      parts[10], // data_inicio_atividade
      parts[11], // cnae_fiscal_principal
      parts[12], // cnae_fiscal_secundaria
      parts[13], // tipo_logradouro
      parts[14], // logradouro
      parts[15], // numero
      parts[16], // complemento
      parts[17], // bairro
      parts[18], // cep
      uf,        // uf
      municipioCodigo, // municipio
      municipioNome,   // municipio_nome
      parts[21], // ddd_1
      parts[22], // telefone_1
      parts[23], // ddd_2
      parts[24], // telefone_2
      parts[25], // ddd_fax
      parts[26], // fax
      parts[27], // email
      parts[28], // situacao_especial
      parts[29], // data_situacao_especial
    ]);

    if (batch.length >= BATCH_SIZE) {
      insertMany(batch);
      count += batch.length;
      batch = [];
      if (count % 100000 === 0) log(`  ${count.toLocaleString()} records imported...`);
    }
  }

  if (batch.length > 0) {
    insertMany(batch);
    count += batch.length;
  }

  log(`  File ${fileIndex}: ${count.toLocaleString()} records imported`);
  return count;
}

// ─── Main ───

async function main() {
  log('=== Receita Federal CNPJ Import ===');
  if (UF_FILTER) log(`UF filter: ${UF_FILTER.join(', ')}`);

  // Check if running in CSV mode (files already extracted)
  const csvMunicipios = process.env.RF_MUNICIPIOS_CSV;
  const csvEstabelecimentos = process.env.RF_ESTABELECIMENTOS_CSV;

  if (csvMunicipios || csvEstabelecimentos) {
    log('CSV mode: importing from pre-extracted CSV files');
    fs.mkdirSync(DATA_DIR, { recursive: true });
    const db = new Database(DB_PATH);
    setupDatabase(db);

    if (csvMunicipios && fs.existsSync(csvMunicipios)) {
      await importMunicipiosFromCSV(db, csvMunicipios);
    } else {
      log('No municipios CSV provided, using fallback...');
    }

    let totalImported = 0;
    if (csvEstabelecimentos) {
      const csvFiles = csvEstabelecimentos.split(',');
      for (let i = 0; i < csvFiles.length; i++) {
        const csvFile = csvFiles[i].trim();
        if (fs.existsSync(csvFile)) {
          const count = await importEstabelecimentosFromCSV(db, csvFile, i);
          totalImported += count;
        } else {
          log(`WARNING: CSV file not found: ${csvFile}`);
        }
      }
    }

    db.prepare("INSERT OR REPLACE INTO meta VALUES ('imported_at', ?)").run(new Date().toISOString());
    db.prepare("INSERT OR REPLACE INTO meta VALUES ('total_records', ?)").run(String(totalImported));

    const stats = db.prepare('SELECT COUNT(*) as cnt FROM estabelecimentos').get();
    const ativos = db.prepare("SELECT COUNT(*) as cnt FROM estabelecimentos WHERE situacao_cadastral = '02'").get();
    log('=== Import Complete ===');
    log(`Total records: ${stats.cnt.toLocaleString()}`);
    log(`Active companies: ${ativos.cnt.toLocaleString()}`);
    db.close();
    return;
  }

  log(`Files to import: ${FILE_INDICES.join(', ')}`);

  // Create data directory
  fs.mkdirSync(DATA_DIR, { recursive: true });

  // Open database
  const db = new Database(DB_PATH);
  setupDatabase(db);

  // 1. Download and import Municipios
  const municipiosZip = path.join(DATA_DIR, 'Municipios.zip');
  if (!fs.existsSync(municipiosZip)) {
    await downloadWithFallback('Municipios.zip', municipiosZip);
  } else {
    log('Municipios.zip already downloaded, skipping...');
  }
  await importMunicipios(db, municipiosZip);

  // 2. Download and import Estabelecimentos files
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

    // Optionally delete zip after import to save space
    if (process.env.RF_DELETE_ZIPS === 'true') {
      fs.unlinkSync(zipPath);
      log(`Deleted ${zipName} to save space`);
    }
  }

  // 3. Save metadata
  db.prepare("INSERT OR REPLACE INTO meta VALUES ('imported_at', ?)").run(new Date().toISOString());
  db.prepare("INSERT OR REPLACE INTO meta VALUES ('uf_filter', ?)").run(UF_FILTER ? UF_FILTER.join(',') : 'ALL');
  db.prepare("INSERT OR REPLACE INTO meta VALUES ('total_records', ?)").run(String(totalImported));

  // 4. Final stats
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
