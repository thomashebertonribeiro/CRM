/**
 * Receita Federal Local Database Service
 *
 * Downloads the public CNPJ data from Receita Federal,
 * imports into a local SQLite database, and provides
 * search by CNAE + municipality.
 *
 * RF data source: https://dadosabertos.rfb.gov.br/CNPJ/
 * Layout: Estabelecimentos0.zip ... Estabelecimentos9.zip
 * Columns (semicolon-separated, no header):
 *   0  cnpj_basico
 *   1  cnpj_ordem
 *   2  cnpj_dv
 *   3  identificador_matriz_filial  (1=matriz, 2=filial)
 *   4  nome_fantasia
 *   5  situacao_cadastral           (1=nula,2=ativa,3=suspensa,4=inapta,8=baixada)
 *   6  data_situacao_cadastral
 *   7  motivo_situacao_cadastral
 *   8  nome_cidade_exterior
 *   9  pais
 *  10  data_inicio_atividade
 *  11  cnae_fiscal_principal
 *  12  cnae_fiscal_secundaria
 *  13  tipo_logradouro
 *  14  logradouro
 *  15  numero
 *  16  complemento
 *  17  bairro
 *  18  cep
 *  19  uf
 *  20  municipio  (IBGE code)
 *  21  ddd_1
 *  22  telefone_1
 *  23  ddd_2
 *  24  telefone_2
 *  25  ddd_fax
 *  26  fax
 *  27  email
 *  28  situacao_especial
 *  29  data_situacao_especial
 */

import Database from 'better-sqlite3';
import AdmZip from 'adm-zip';
import fs from 'fs';
import path from 'path';
import https from 'https';
import http from 'http';
import { createReadStream } from 'fs';
import readline from 'readline';
import logger from '../utils/logger.js';

const DATA_DIR = process.env.RF_DATA_DIR || './data/rf';
const DB_PATH = path.join(DATA_DIR, 'cnpj.db');

// RF public data base URL
const RF_BASE_URL = 'https://dadosabertos.rfb.gov.br/CNPJ';

// Municipios table (IBGE code → name) — needed to map city names to codes
// We'll build this from the Municipios.zip file from RF
const MUNICIPIOS_URL = `${RF_BASE_URL}/Municipios.zip`;

let _db = null;

function getDb() {
  if (!_db) {
    if (!fs.existsSync(DB_PATH)) {
      throw new Error('Banco de dados da Receita Federal não encontrado. Execute o script de importação primeiro: node src/apps/api/scripts/importRF.js');
    }
    _db = new Database(DB_PATH, { readonly: true });
    _db.pragma('journal_mode = WAL');
    _db.pragma('cache_size = -64000'); // 64MB cache
  }
  return _db;
}

function isDbReady() {
  try {
    if (!fs.existsSync(DB_PATH)) return false;
    const db = new Database(DB_PATH, { readonly: true });
    const row = db.prepare("SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' AND name='estabelecimentos'").get();
    db.close();
    return row && row.cnt > 0;
  } catch {
    return false;
  }
}

function getDbStats() {
  try {
    const db = getDb();
    const total = db.prepare('SELECT COUNT(*) as cnt FROM estabelecimentos').get();
    const ativos = db.prepare("SELECT COUNT(*) as cnt FROM estabelecimentos WHERE situacao_cadastral = '02'").get();
    const lastUpdate = db.prepare("SELECT value FROM meta WHERE key = 'imported_at'").get();
    return {
      ready: true,
      total_empresas: total?.cnt || 0,
      empresas_ativas: ativos?.cnt || 0,
      ultima_atualizacao: lastUpdate?.value || null,
    };
  } catch (e) {
    return { ready: false, error: e.message };
  }
}

/**
 * Search companies by CNAE + municipality name
 */
function searchByCnaeAndCity(cnae, cidade, limit = 50, offset = 0, activeOnly = true) {
  const db = getDb();

  // Normalize city name for search
  const cidadeNorm = cidade.toUpperCase().trim()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, ''); // remove accents

  // Find municipio codes matching the city name
  const municipios = db.prepare(
    "SELECT codigo FROM municipios WHERE nome_normalizado LIKE ?"
  ).all(`%${cidadeNorm}%`);

  if (municipios.length === 0) {
    return { total: 0, limit, offset, empresas: [] };
  }

  const municipioCodes = municipios.map(m => m.codigo);
  const placeholders = municipioCodes.map(() => '?').join(',');

  // Clean CNAE (remove non-digits)
  const cnaeClean = cnae.replace(/\D/g, '');

  let whereClause = `(cnae_fiscal_principal = ? OR cnae_fiscal_secundaria LIKE ?) AND municipio IN (${placeholders})`;
  const params = [cnaeClean, `%${cnaeClean}%`, ...municipioCodes];

  if (activeOnly) {
    whereClause += " AND situacao_cadastral = '02'";
  }

  const countRow = db.prepare(
    `SELECT COUNT(*) as cnt FROM estabelecimentos WHERE ${whereClause}`
  ).get(...params);

  const total = countRow?.cnt || 0;

  const rows = db.prepare(
    `SELECT * FROM estabelecimentos WHERE ${whereClause} LIMIT ? OFFSET ?`
  ).all(...params, limit, offset);

  const empresas = rows.map(formatRow);

  return { total, limit, offset, empresas };
}

function formatRow(row) {
  const cnpj = `${row.cnpj_basico}${row.cnpj_ordem}${row.cnpj_dv}`;
  return {
    cnpj: cnpj,
    cnpjFormatado: formatCNPJ(cnpj),
    nomeFantasia: row.nome_fantasia || '',
    situacaoCadastral: mapSituacao(row.situacao_cadastral),
    dataInicioAtividade: row.data_inicio_atividade || '',
    cnae: {
      codigo: row.cnae_fiscal_principal || '',
      secundarios: row.cnae_fiscal_secundaria || '',
    },
    endereco: {
      tipoLogradouro: row.tipo_logradouro || '',
      logradouro: row.logradouro || '',
      numero: row.numero || '',
      complemento: row.complemento || '',
      bairro: row.bairro || '',
      cep: row.cep || '',
      municipio: row.municipio_nome || '',
      uf: row.uf || '',
    },
    contato: {
      telefone1: row.ddd_1 && row.telefone_1 ? `(${row.ddd_1}) ${row.telefone_1}` : '',
      telefone2: row.ddd_2 && row.telefone_2 ? `(${row.ddd_2}) ${row.telefone_2}` : '',
      email: row.email || '',
    },
    tipo: row.identificador_matriz_filial === '1' ? 'MATRIZ' : 'FILIAL',
  };
}

function formatCNPJ(cnpj) {
  const c = cnpj.replace(/\D/g, '').padStart(14, '0');
  return `${c.slice(0,2)}.${c.slice(2,5)}.${c.slice(5,8)}/${c.slice(8,12)}-${c.slice(12,14)}`;
}

function mapSituacao(code) {
  const map = { '01': 'NULA', '02': 'ATIVA', '03': 'SUSPENSA', '04': 'INAPTA', '08': 'BAIXADA' };
  return map[code] || code || 'DESCONHECIDA';
}

export { getDb, isDbReady, getDbStats, searchByCnaeAndCity };
