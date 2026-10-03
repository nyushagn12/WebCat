document.addEventListener('DOMContentLoaded', () => {
  const result = document.getElementById('result');
  const load = document.getElementById('load');
  load.addEventListener('click', async () => {
    result.textContent = 'Fetching /api/message ...';
    try {
      const response = await fetch('/api/message');
      const data = await response.json();
      result.textContent = `HTTP ${response.status}\n${JSON.stringify(data, null, 2)}`;
    } catch (err) {
      result.textContent = 'Fetch failed: ' + err;
    }
  });
});
