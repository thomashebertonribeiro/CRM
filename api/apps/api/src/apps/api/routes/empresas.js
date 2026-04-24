import express from 'express';
import logger from '../utils/logger.js';
import { searchByActivity } from '../services/cnpjService.js';
import { isDbReady, getDbStats, searchByCnaeAndCity } from '../services/rfDatabase.js';

const router = express.Router();

/**
 * GET /empresas/status
 * Returns the status of the local RF database
 */
router.get('/status', (_req, res) => {
  const stats = getDbStats();
  res.json(stats);
});

/**
 * GET /empresas/buscar?cnae=9521500&cidade=Londrina&limit=50&offset=0&active_only=true
 * Search companies by CNAE + city.
 * Uses local RF database if available, falls back to ReceitaWS otherwise.
 */
router.get('/buscar', async (req, res, next) => {
  try {
    const { cnae, cidade, limit = 50, offset = 0, active_only = 'true' } = req.query;

    if (!cnae) return res.status(400).json({ error: 'CNAE é obrigatório' });
    if (!cidade) return res.status(400).json({ error: 'Cidade é obrigatória' });

    const activeOnly = active_only !== 'false';

    // Use local RF database if available (much better results)
    if (isDbReady()) {
      const results = searchByCnaeAndCity(
        cnae, cidade,
        Math.min(parseInt(limit, 10) || 50, 200),
        parseInt(offset, 10) || 0,
        activeOnly
      );
      logger.info(`RF DB search: CNAE=${cnae}, City=${cidade}, Total=${results.total}`);
      return res.json({ ...results, source: 'receita_federal_local' });
    }

    // Fallback to ReceitaWS (limited)
    logger.warn('RF database not ready, falling back to ReceitaWS');
    const results = await searchByActivity(cnae, cidade, limit, offset);
    logger.info(`ReceitaWS search: CNAE=${cnae}, City=${cidade}, Total=${results.total}`);
    res.json({ ...results, source: 'receitaws' });
  } catch (err) {
    next(err);
  }
});

export default router;
