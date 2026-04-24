import { Router } from 'express';
import healthCheck from './health-check.js';
import cnpjRouter from './cnpj.js';
import empresasRouter from './empresas.js';

const router = Router();

export default () => {
  router.get('/health', healthCheck);
  router.use('/cnpj', cnpjRouter);
  router.use('/empresas', empresasRouter);
  return router;
};
