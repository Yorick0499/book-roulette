document.getElementById('import-form').addEventListener('submit', function () {
  this.querySelector('button').disabled = true;
  this.setAttribute('aria-busy', 'true');
  document.getElementById('import-progress').hidden = false;
});
