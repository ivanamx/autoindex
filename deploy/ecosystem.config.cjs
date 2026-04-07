/**
 * PM2: Gunicorn sirve la app Flask en 127.0.0.1:8000 (nginx hace proxy).
 *
 * Cambia `root` si el código no está en esta ruta en el servidor.
 *
 *   cd /home/ivanam/projects/autoindex
 *   pm2 start deploy/ecosystem.config.cjs
 *   pm2 save
 *   pm2 startup   # seguir instrucciones para arranque al reiniciar el servidor
 */

const root = '/home/ivanam/projects/autoindex';

module.exports = {
  apps: [
    {
      name: 'autoindex',
      cwd: root,
      script: `${root}/venv/bin/gunicorn`,
      args:
        '-w 3 -b 127.0.0.1:8000 --timeout 120 --access-logfile - --error-logfile - app:app',
      interpreter: 'none',
      autorestart: true,
      max_restarts: 15,
      min_uptime: '10s',
      env: {
        PYTHONUNBUFFERED: '1',
      },
    },
  ],
};
