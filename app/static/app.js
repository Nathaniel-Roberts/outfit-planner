// Small behaviours. No framework.
(function () {
  function init(root) {
    // A file input marked data-autosubmit submits its form as soon as a photo is picked.
    root.querySelectorAll('input[type=file][data-autosubmit]').forEach(function (input) {
      if (input.dataset.bound) return;
      input.dataset.bound = '1';
      input.addEventListener('change', function () {
        if (!input.files || !input.files.length) return;
        var form = input.form;
        var label = input.closest('label');
        if (label) { label.querySelector('span').textContent = 'Uploading…'; label.style.opacity = '0.7'; }
        form.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      });
    });
    root.querySelectorAll('input[type=file][data-filename-label]').forEach(function (input) {
      if (input.dataset.bound) return;
      input.dataset.bound = '1';
      input.addEventListener('change', function () {
        var label = input.closest('label');
        if (label && input.files && input.files.length) label.querySelector('span').textContent = 'Photo ready: ' + input.files[0].name;
      });
    });
    // Temperature presets fill the min and max fields.
    root.querySelectorAll('[data-temp-presets] button').forEach(function (btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', function () {
        var form = btn.form;
        form.querySelector('[name=temp_min]').value = btn.dataset.min;
        form.querySelector('[name=temp_max]').value = btn.dataset.max;
        btn.parentElement.querySelectorAll('button').forEach(function (b) { b.classList.remove('on'); });
        btn.classList.add('on');
      });
    });
    // Colour pairing hint, if the page has one.
    var hint = root.querySelector('#colour-hint');
    if (hint && !hint.dataset.bound) {
      hint.dataset.bound = '1';
      var boxes = root.querySelectorAll('input[name=colours]');
      var update = function () {
        var picked = Array.prototype.filter.call(boxes, function (b) { return b.checked; }).map(function (b) { return b.value; });
        if (picked.length < 2) { hint.textContent = ''; return; }
        fetch('/colour-rules/hint?colours=' + encodeURIComponent(picked.join(',')), { headers: { accept: 'text/plain' } })
          .then(function (r) { return r.ok ? r.text() : ''; })
          .then(function (t) { hint.textContent = t; })
          .catch(function () {});
      };
      boxes.forEach(function (b) { b.addEventListener('change', update); });
      update();
    }
  }
  document.addEventListener('DOMContentLoaded', function () { init(document); });
  document.addEventListener('htmx:afterSettle', function (e) { init(e.target || document); });
  if (document.readyState !== 'loading') init(document);
})();
