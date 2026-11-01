(function () {
  'use strict';
  function updateCover(image) {
    image.classList.toggle('is-loaded', image.complete && image.naturalWidth > 0);
  }
  for (const image of document.querySelectorAll('.book-cover-image')) {
    image.addEventListener('load', () => updateCover(image));
    image.addEventListener('error', () => image.classList.remove('is-loaded'));
    updateCover(image);
  }
  window.BookCovers = { set(image, url) {
    image.classList.remove('is-loaded');
    image.hidden = !url;
    if (url) image.src = url;
    else image.removeAttribute('src');
    updateCover(image);
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
