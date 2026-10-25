/* TrustGate glass progressive enhancement (decorative surfaces only).
 * Wraps elements marked data-glass in liquid-glass-js (MIT, vendored in this folder).
 * Never touches forms, buttons, approval/payment content. On ANY failure the original element is left
 * exactly as it was (it keeps a CSS backdrop-filter look). Text stays real DOM, so labels such as
 * "Simulated payments" remain accessible and legible regardless of the WebGL result. */
(function () {
  'use strict';
  var hosts = Array.prototype.slice.call(document.querySelectorAll('[data-glass]'));
  if (!hosts.length) return;

  function webglOk() {
    try {
      var c = document.createElement('canvas');
      return !!(c.getContext('webgl') || c.getContext('experimental-webgl'));
    } catch (e) { return false; }
  }
  var mq = function (q) { return window.matchMedia && window.matchMedia(q).matches; };
  if (mq('(prefers-reduced-motion: reduce)') || mq('(prefers-reduced-transparency: reduce)')) return;
  if (window.innerWidth < 860 || window.devicePixelRatio > 3 || !webglOk()) return;
  if (/[?&]glass=off\b/.test(location.search)) return; // kill switch for recording / debugging

  function load(src) {
    return new Promise(function (res, rej) {
      var s = document.createElement('script');
      s.src = src; s.onload = res; s.onerror = function () { rej(new Error('load ' + src)); };
      document.head.appendChild(s);
    });
  }
  var base = document.currentScript ? document.currentScript.src.replace(/glass-init\.js.*$/, '') : '/static/glass/';

  function enhance() {
    window.scrollTo(0, 0); // the library samples a page snapshot by viewport position
    var made = [];
    hosts.forEach(function (el) {
      el.classList.add('glass-on');                      // transparent bg so it is not baked into the snapshot
      el.setAttribute('data-html2canvas-ignore', 'true'); // and its own text is not refracted twice
    });
    hosts.forEach(function (el) {
      var r = parseFloat(getComputedStyle(el).borderTopLeftRadius) || 12;
      var c = new Container({ borderRadius: r, type: 'rounded', tintOpacity: parseFloat(el.getAttribute('data-glass-tint') || '0.12') });
      c.element.classList.add('glass-host');
      el.parentNode.insertBefore(c.element, el);
      c.element.appendChild(el);
      c.updateSizeFromDOM();
      made.push([c, el]);
    });
    // If the snapshot fails or times out, restore the plain look.
    setTimeout(function () {
      if (Container.pageSnapshot) return;
      made.forEach(function (p) {
        var c = p[0], el = p[1];
        if (c.element.parentNode) { c.element.parentNode.insertBefore(el, c.element); c.element.remove(); }
        el.classList.remove('glass-on'); el.removeAttribute('data-html2canvas-ignore');
      });
    }, 6000);
  }

  window.addEventListener('load', function () {
    // let the entrance animations settle before the page snapshot is taken
    setTimeout(function () {
      load(base + 'html2canvas.min.js').then(function () { return load(base + 'container.js'); })
        .then(enhance).catch(function (e) { if (window.console) console.warn('glass disabled:', e.message); });
    }, 1100);
  });
})();
