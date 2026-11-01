(function () {
  'use strict';
  for (const form of document.querySelectorAll('.online-search')) {
    form.addEventListener('submit', () => {
      form.querySelector('button').disabled = true;
      form.setAttribute('aria-busy', 'true');
      form.querySelector('.search-progress').hidden = false;
    });
  }
  const status = document.getElementById('download-status');
  if (!status || status.dataset.running !== 'true') return;
  const names = { running: 'Pobieranie trwa', completed: 'Pobieranie zakończone — odśwież katalog', failed: 'Pobieranie nie powiodło się', cancelled: 'Pobieranie anulowane', interrupted: 'Pobieranie przerwane restartem' };
  async function poll() {
    try {
      const response = await fetch(status.dataset.statusUrl, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error();
      const { job } = await response.json();
      if (!job) return;
      document.getElementById('download-state').textContent = `${names[job.state] || job.state} · ${job.phase === 'authors' ? 'Autorzy' : job.phase === 'works' ? 'Utwory' : 'Przygotowanie'}`;
      const size = `${(job.downloaded / 1048576).toFixed(1)} MiB`;
      const percentage = job.total ? ` (${Math.min(100, 100 * job.downloaded / job.total).toFixed(1)}%)` : '';
      document.getElementById('download-count').textContent = `Przetworzono: ${job.records} rekordów · pominięto: ${job.skipped} · pobrano w tej fazie: ${size}${percentage}`;
      document.getElementById('download-error').textContent = job.error;
      if (job.state !== 'running') {
        const form = document.querySelector('form[action$="/catalog/cancel"]');
        if (form) form.hidden = true;
        return;
      }
    } catch {
      document.getElementById('download-error').textContent = 'Nie można odczytać postępu. Sprawdź, czy serwer działa; odczyt zostanie ponowiony.';
    }
    setTimeout(poll, 2500);
  }
  poll();
}());
