import logger from '../utils/logger.js';
import cacheManager from '../utils/cache.js';

function validateCNPJFormat(cnpj) {
  const clean = cnpj.replace(/\D/g, '');
  return clean.length === 14 && /^\d{14}$/.test(clean);
}

function formatAddress(d) {
  return {
    logradouro: d.logradouro || '',
    numero: d.numero || '',
    complemento: d.complemento || '',
    bairro: d.bairro || '',
    cidade: d.municipio || '',
    estado: d.uf || '',
    cep: d.cep || '',
  };
}

function formatCNAE(d) {
  return {
    codigo: d.cnae_fiscal || '',
    descricao: d.cnae_fiscal_descricao || '',
  };
}

function formatCompanyData(d) {
  return {
    cnpj: d.cnpj || '',
    razaoSocial: d.nome || '',
    nomeFantasia: d.fantasia || '',
    endereco: formatAddress(d),
    situacao: d.situacao || '',
    dataAbertura: d.abertura || '',
    dataUltimaAlteracao: d.ultima_atualizacao || '',
    cnae: formatCNAE(d),
    cnaesSecundarias: Array.isArray(d.atividades_secundarias)
      ? d.atividades_secundarias.map(c => ({ codigo: c.code || '', descricao: c.text || '' }))
      : [],
    naturezaJuridica: d.natureza_juridica || '',
    capitalSocial: d.capital_social || '',
    telefone: d.telefone || '',
    email: d.email || '',
    porte: d.porte || '',
    socios: Array.isArray(d.qsa)
      ? d.qsa.map(s => ({ nome: s.nome || '', cargo: s.qual || '' }))
      : [],
  };
}

async function fetchEnrichedCNPJ(cnpj) {
  if (!validateCNPJFormat(cnpj)) {
    throw Object.assign(new Error('CNPJ inválido: formato incorreto'), { status: 400 });
  }

  const clean = cnpj.replace(/\D/g, '');
  const cacheKey = `cnpj:${clean}`;

  const cached = cacheManager.get(cacheKey);
  if (cached) return cached;

  const url = `https://www.receitaws.com.br/v1/cnpj/${clean}`;
  logger.info(`Fetching CNPJ: ${url}`);

  const response = await fetch(url);

  if (response.status === 404) throw Object.assign(new Error('CNPJ não encontrado'), { status: 404 });
  if (!response.ok) throw Object.assign(new Error(`Erro na API da Receita: ${response.status}`), { status: 502 });

  const data = await response.json();

  if (data.status === 'ERROR') {
    throw Object.assign(new Error(data.message || 'CNPJ não encontrado'), { status: 404 });
  }

  const formatted = formatCompanyData(data);
  cacheManager.set(cacheKey, formatted);
  return formatted;
}

async function searchByActivity(cnae, cidade, limit = 50, offset = 0) {
  if (!cnae || typeof cnae !== 'string') {
    throw Object.assign(new Error('CNAE é obrigatório'), { status: 400 });
  }

  const cleanCNAE = cnae.replace(/\D/g, '');
  if (!cleanCNAE || !/^\d+$/.test(cleanCNAE)) {
    throw Object.assign(new Error('CNAE deve conter apenas números'), { status: 400 });
  }

  if (!cidade || typeof cidade !== 'string' || cidade.trim().length === 0) {
    throw Object.assign(new Error('Cidade é obrigatória'), { status: 400 });
  }

  let parsedLimit = Math.min(Math.max(parseInt(limit, 10) || 50, 1), 100);
  let parsedOffset = Math.max(parseInt(offset, 10) || 0, 0);

  const cacheKey = `search:${cleanCNAE}:${cidade.toLowerCase().trim()}`;
  let allResults = cacheManager.get(cacheKey);

  if (!allResults) {
    // ReceitaWS free plan only supports CNPJ lookup, not CNAE search.
    // Using the public ReceitaWS CNAE endpoint (available on paid plans).
    // For free usage, we query the BrasilAPI CNPJ endpoint with CNAE filter.
    const url = `https://brasilapi.com.br/api/cnpj/v1/search?cnae=${cleanCNAE}&municipio=${encodeURIComponent(cidade.toUpperCase().trim())}`;
    logger.info(`Searching companies: ${url}`);

    const response = await fetch(url, {
      headers: { 'Accept': 'application/json' },
    });

    if (!response.ok) {
      logger.error(`API error: ${response.status}`);
      throw Object.assign(new Error(`Erro ao buscar empresas: ${response.status}`), { status: 502 });
    }

    const data = await response.json();
    let empresas = Array.isArray(data) ? data : (data.data || []);

    if (empresas.length === 0) {
      return { total: 0, limit: parsedLimit, offset: parsedOffset, empresas: [] };
    }

    allResults = empresas.map(formatCompanyData);
    cacheManager.set(cacheKey, allResults);
    logger.info(`Cached ${allResults.length} results for CNAE=${cleanCNAE}, City=${cidade}`);
  }

  const total = allResults.length;
  const paginated = allResults.slice(parsedOffset, parsedOffset + parsedLimit);

  return { total, limit: parsedLimit, offset: parsedOffset, empresas: paginated };
}

export { fetchEnrichedCNPJ, searchByActivity };
