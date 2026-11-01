(function () {
  'use strict';
  function updateCover(image) {
    image.classList.toggle('is-loaded', image.complete && image.naturalWidth > 0);
  }
  const versions = new WeakMap();
  async function loadCover(image, url) {
    const version = (versions.get(image) || 0) + 1;
    versions.set(image, version);
    image.classList.remove('is-loaded');
    image.removeAttribute('src');
    const note = image.parentElement.querySelector('.cover-status');
    if (note) note.textContent = url ? 'Ładowanie okładki…' : '';
    if (!url) return;
    const deadline = Date.now() + 45000;
    let delay = 500;
    while (versions.get(image) === version && image.isConnected) {
      try {
        const response = await fetch(url, { headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (versions.get(image) !== version) return;
        if (data.state === 'ready') {
          image.src = data.url;
          return;
        }
        if (data.state !== 'pending') {
          if (note) note.textContent = data.state === 'missing' ? 'Nie znaleziono okładki w Open Library' : 'Okładka chwilowo niedostępna';
          return;
        }
      } catch {
        if (note) note.textContent = 'Nie można teraz wczytać okładki';
        return;
      }
      if (Date.now() > deadline) {
        if (note) note.textContent = 'Okładka jeszcze się ładuje. Odśwież stronę za chwilę.';
        return;
      }
      await new Promise(resolve => setTimeout(resolve, delay));
      delay = Math.min(2500, delay * 1.4);
    }
  }
  const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        observer.unobserve(entry.target);
        loadCover(entry.target, entry.target.dataset.coverUrl);
      }
    }
  }, { rootMargin: '100px' }) : null;
  for (const image of document.querySelectorAll('.book-cover-image')) {
    image.addEventListener('load', () => updateCover(image));
    image.addEventListener('error', () => {
      image.classList.remove('is-loaded');
      const note = image.parentElement.querySelector('.cover-status');
      if (note) note.textContent = 'Okładka chwilowo niedostępna';
    });
    if (image.dataset.coverUrl) {
      if (observer) observer.observe(image);
      else loadCover(image, image.dataset.coverUrl);
    }
  }
  window.BookCovers = { set(image, url) {
    image.hidden = !url;
    loadCover(image, url);
  } };
  const toast = document.getElementById('status-message');
  let toastTimeout;
  for (const form of document.querySelectorAll('.selection-form')) {
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      const button = form.querySelector('button');
      const action = form.querySelector('[name=action]');
      const selecting = action.value === 'select';
      button.disabled = true;
      try {
        const response = await fetch(form.getAttribute('action'), { method: 'POST', body: new FormData(form), headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error('Nie zapisano wyboru. Odśwież stronę i spróbuj ponownie.');
        const data = await response.json();
        action.value = selecting ? 'deselect' : 'select';
        button.setAttribute('aria-pressed', String(selecting));
        button.textContent = selecting ? '✓ W losowaniu' : '+ Dodaj do losowania';
        form.closest('.book-card').classList.toggle('is-selected', selecting);
        for (const counter of document.querySelectorAll('[data-selected-count]')) counter.textContent = data.selected;
        toast.textContent = selecting ? 'Książka dodana do losowania.' : 'Książka usunięta z losowania.';
      } catch (error) {
        toast.textContent = error.message;
      } finally {
        button.disabled = false;
        toast.hidden = false;
        clearTimeout(toastTimeout);
        toastTimeout = setTimeout(() => { toast.hidden = true; }, 4500);
      }
    });
  }
  for (const time of document.querySelectorAll('time[datetime]')) {
    const date = new Date(time.dateTime);
    if (!Number.isNaN(date.getTime())) time.textContent = new Intl.DateTimeFormat('pl-PL', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  }
}());
