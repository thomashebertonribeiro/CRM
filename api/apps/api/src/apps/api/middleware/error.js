import logger from '../utils/logger.js';

/**
 * Global error handling middleware
 */
export function errorMiddleware(err, req, res, next) {
  logger.error('Unhandled error:', err.message || err);

  const status = err.status || err.statusCode || 500;
  const message = err.message || 'Internal server error';

  res.status(status).json({ error: message });
}
