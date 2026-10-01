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
    // Weather override: the summary button toggles the full-width form below the card row.
    root.querySelectorAll('.override summary').forEach(function (sum) {
      if (sum.dataset.bound) return;
      sum.dataset.bound = '1';
      sum.addEventListener('click', function (e) {
        e.preventDefault();
        var form = document.getElementById('override-form');
        if (form) { form.hidden = !form.hidden; if (!form.hidden) form.querySelector('input').focus(); }
      });
    });
    // Lightbox: tap a photo in the strip for full screen, swipe between, double-tap to zoom.
    root.querySelectorAll('[data-lightbox] img').forEach(function (img) {
      if (img.dataset.bound) return;
      img.dataset.bound = '1';
      img.style.cursor = 'zoom-in';
      img.addEventListener('click', function () {
        var strip = img.closest('[data-lightbox]');
        var imgs = Array.prototype.slice.call(strip.querySelectorAll('img'));
        var box = document.createElement('div');
        box.className = 'lightbox';
        box.setAttribute('role', 'dialog');
        box.setAttribute('aria-label', 'Photo viewer');
        var track = document.createElement('div');
        track.className = 'track';
        imgs.forEach(function (src) {
          var full = document.createElement('img');
          full.src = src.dataset.full || src.src;
          full.alt = src.alt || '';
          var last = 0;
          full.addEventListener('click', function (e) {
            var now = Date.now();
            if (now - last < 320) { full.classList.toggle('zoom'); }
            last = now;
          });
          track.appendChild(full);
        });
        var close = document.createElement('button');
        close.className = 'close'; close.type = 'button'; close.setAttribute('aria-label', 'Close'); close.textContent = '×';
        var count = document.createElement('div'); count.className = 'count';
        function updateCount() {
          var i = Math.round(track.scrollLeft / Math.max(1, track.clientWidth)) + 1;
          count.textContent = imgs.length > 1 ? i + ' of ' + imgs.length : '';
        }
        track.addEventListener('scroll', updateCount, { passive: true });
        function dismiss() { box.remove(); document.body.classList.remove('has-lightbox'); document.removeEventListener('keydown', onKey); }
        function onKey(e) { if (e.key === 'Escape') dismiss(); }
        close.addEventListener('click', dismiss);
        document.addEventListener('keydown', onKey);
        box.appendChild(track); box.appendChild(close); box.appendChild(count);
        document.body.appendChild(box);
        document.body.classList.add('has-lightbox');
        track.scrollLeft = imgs.indexOf(img) * track.clientWidth;
        updateCount();
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
