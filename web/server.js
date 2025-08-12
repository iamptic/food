const express = require('express');
const path = require('path');
const app = express();

// 1) Основная статика: папка web/
const WEB_DIR = path.join(__dirname, 'web');

// 2) Отдаём web/ и всё под /web/*
app.use('/web', express.static(WEB_DIR, { index: 'index.html', extensions: ['html'] }));
app.use(express.static(WEB_DIR, { index: 'index.html', extensions: ['html'] }));

// 3) Фолбэк: если страницы лежат прямо в корне репо (buyer/, merchant/)
app.use(express.static(__dirname, { index: 'index.html', extensions: ['html'] }));

// /health для проверок
app.get('/health', (req,res) => res.json({ ok: true }));

// Корень: если есть web/buyer — редирект туда, иначе в /buyer/
app.get('/', (req, res) => {
  res.redirect('/web/buyer/');
});

const PORT = process.env.PORT || 8080;
app.listen(PORT, () => console.log('Foody web listening on', PORT));
