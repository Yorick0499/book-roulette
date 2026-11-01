(function () {
  'use strict';
  const app = document.getElementById('wheel-app');
  if (!app) return;
  const canvas = document.getElementById('wheel');
  const context = canvas.getContext('2d');
  const button = document.getElementById('spin');
  const again = document.getElementById('again');
  const status = document.getElementById('wheel-status');
  const result = document.getElementById('result');
  let pool = JSON.parse(document.getElementById('wheel-data').textContent);
  let key = app.dataset.drawKey;
  let rotation = 0;
  let busy = false;
  const colors = ['#455b45', '#d5bc98', '#78846b', '#b98666', '#8f9f8a', '#aa8e6b'];

  function paint(books) {
    context.clearRect(0, 0, 1000, 1000);
    const n = Math.max(books.length, 1);
    const arc = Math.PI * 2 / n;
    for (let i = 0; i < n; i++) {
      const start = -Math.PI / 2 + i * arc;
      context.beginPath();
      context.moveTo(500, 500);
      context.arc(500, 500, 482, start, start + arc);
      context.closePath();
      context.fillStyle = books.length ? colors[i % colors.length] : '#dce1cf';
      context.fill();
      // At large counts thin borders would hide the colored segments.
      if (n <= 100) {
        context.strokeStyle = '#f7f3e9';
        context.lineWidth = 2;
        context.stroke();
      }
      const labelEvery = Math.max(1, Math.ceil(n / 32));
      if (books.length && i % labelEvery === 0) {
        context.save();
        context.translate(500, 500);
        context.rotate(start + arc / 2);
        context.textAlign = 'right';
        context.textBaseline = 'middle';
        context.fillStyle = i % colors.length === 0 ? '#ffffff' : '#1f261e';
        context.font = n > 20 ? 'bold 21px system-ui' : 'bold 24px system-ui';
        let label = n > 20 ? String(i + 1) : books[i].title;
        while (context.measureText(label).width > 280) label = label.slice(0, -2) + '…';
        context.fillText(label, 448, 0);
        context.restore();
      }
    }
  }

  function updatePool(books) {
    pool = books;
    paint(pool);
    document.getElementById('pool-count').textContent = `W losowaniu: ${pool.length} książek.`;
    const list = document.getElementById('candidate-list');
    list.replaceChildren();
    const fragment = document.createDocumentFragment();
    for (const book of books) {
      const item = document.createElement('li');
      item.textContent = `${book.title} — ${book.author || 'Autor nieznany'}`;
      fragment.appendChild(item);
    }
    list.appendChild(fragment);
    document.querySelector('.candidate-list summary').textContent = `Książki na kole (${books.length})`;
  }

  function showResult(book) {
    document.getElementById('result-title').textContent = book.title;
    document.getElementById('result-author').textContent = book.author || 'Autor nieznany';
    document.getElementById('result-shelf').textContent = book.exclusive_shelf || 'Bez półki';
    document.getElementById('result-initial').textContent = book.title.slice(0, 1);
    const link = document.getElementById('result-goodreads');
    const identifier = book.goodreads_id;
    link.hidden = !identifier || !/^[0-9]{1,20}$/.test(identifier);
    if (!link.hidden) link.href = `https://www.goodreads.com/book/show/${identifier}`;
    else link.removeAttribute('href');
    const openLibrary = document.getElementById('result-openlibrary');
    const workId = book.open_library_id;
    openLibrary.hidden = !workId || !/^\/works\/OL[0-9]{1,18}W$/.test(workId);
    if (!openLibrary.hidden) openLibrary.href = `https://openlibrary.org${workId}`;
    else openLibrary.removeAttribute('href');
    BookCovers.set(document.getElementById('result-image'), book.cover_url);
    result.hidden = false;
    result.focus({ preventScroll: true });
    result.scrollIntoView({ behavior: 'auto', block: 'nearest' });
  }

  async function spin() {
    if (busy) return;
    busy = true;
    button.disabled = true;
    again.disabled = true;
    result.hidden = true;
    app.setAttribute('aria-busy', 'true');
    status.textContent = 'Losowanie…';
    try {
      const response = await fetch(app.dataset.drawUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name=csrf-token]').content },
        body: JSON.stringify({ key })
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Losowanie nie powiodło się. Odśwież stronę.');
      }
      const data = await response.json();
      updatePool(data.candidates);
      const target = WheelMath.targetRotation(rotation, data.winner_index, pool.length);
      status.textContent = 'Koło się kręci… Twój następny rozdział jest coraz bliżej.';
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (!reduced && canvas.animate) {
        const animation = canvas.animate([
          { transform: `rotate(${rotation}deg)` }, { transform: `rotate(${target}deg)` }
        ], { duration: 5200, easing: 'cubic-bezier(0.12, 0.72, 0.08, 1)', fill: 'forwards' });
        await animation.finished;
        canvas.style.transform = `rotate(${target}deg)`;
        animation.cancel();
      } else canvas.style.transform = `rotate(${target}deg)`;
      rotation = target;
      key = data.next_key;
      status.textContent = `Wylosowano: ${data.winner.title}.`;
      showResult(data.winner);
    } catch (error) {
      // Keep the request key: a retry after a lost response returns the same draw.
      status.textContent = error.message === 'Failed to fetch'
        ? 'Brak połączenia z serwerem. Spróbuj ponownie — nie zapiszemy wyniku drugi raz.' : error.message;
    } finally {
      busy = false;
      button.disabled = false;
      again.disabled = false;
      app.removeAttribute('aria-busy');
    }
  }
  paint(pool);
  button.addEventListener('click', spin);
  again.addEventListener('click', spin);
}());
