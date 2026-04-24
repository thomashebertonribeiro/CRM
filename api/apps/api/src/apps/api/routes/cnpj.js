import express from 'express';
import logger from '../utils/logger.js';
import { fetchEnrichedCNPJ } from '../services/cnpjService.js';

const router = express.Router();

router.get('/:cnpj', async (req, res, next) => {
  try {
    const { cnpj } = req.params;
    if (!cnpj) return res.status(400).json({ error: 'CNPJ é obrigatório' });

    const data = await fetchEnrichedCNPJ(cnpj);
    logger.info(`CNPJ lookup ok: ${cnpj}`);
    res.json(data);
  } catch (err) {
    next(err);
  }
});

export default router;
