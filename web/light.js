/* Knowlib 沉浸光感（全站公用，支持动态生成元素）：鼠标作为点光源 */
(function () {
  var TILT_SEL = '.hero, .card';                                  // 需要视差倾斜的
  var LIGHT_SEL = '.hero, .card, .qcard, .btn, .back, .filelbl, button'; // 需要发光的
  var last = null, lastTilt = false;

  function setVars(el, e, tilt) {
    var r = el.getBoundingClientRect();
    if (!r.width || !r.height) { return; }
    var x = e.clientX - r.left, y = e.clientY - r.top;
    el.style.setProperty('--mx', x + 'px');
    el.style.setProperty('--my', y + 'px');
    var fx = Math.min(1, Math.max(0, x / r.width));
    el.style.setProperty('--gh', (205 + fx * 60).toFixed(0));      // 色相 205(蓝)->265(紫)
    if (tilt) {
      var fy = Math.min(1, Math.max(0, y / r.height));
      el.style.setProperty('--rx', ((fy - 0.5) * -6).toFixed(2) + 'deg');
      el.style.setProperty('--ry', ((fx - 0.5) * 8).toFixed(2) + 'deg');
    }
  }
  function reset(el, tilt) {
    if (!el) { return; }
    el.style.setProperty('--mx', '-9999px');
    el.style.setProperty('--my', '-9999px');
    if (tilt) { el.style.setProperty('--rx', '0deg'); el.style.setProperty('--ry', '0deg'); }
  }
  function randomSpin(el) {
    if (!el || el.dataset.glowSpin) { return; }
    el.dataset.glowSpin = '1';
    el.style.setProperty('--spin-dur', (5.5 + Math.random() * 5.5).toFixed(2) + 's');
    el.style.setProperty('--spin-delay', (-Math.random() * 12).toFixed(2) + 's');
    el.style.setProperty('--spin-dir', Math.random() < 0.5 ? 'normal' : 'reverse');
  }
  function nearest(node) {
    while (node && node.nodeType === 1) {
      if (node.matches(LIGHT_SEL)) { return node; }
      node = node.parentElement;
    }
    return null;
  }

  function onMove(e) {
    var el = nearest(e.target);
    if (el !== last) {
      reset(last, lastTilt);
      last = el;
      lastTilt = !!(el && el.matches(TILT_SEL) && !document.body.classList.contains('no-tilt'));
      if (el) { randomSpin(el); }
    }
    if (el) { setVars(el, e, lastTilt); }
  }
  document.addEventListener('mousemove', onMove, { passive: true });
  window.addEventListener('blur', function () { reset(last, lastTilt); last = null; }, { passive: true });

  // 动态生成的卡片（如解析后的题卡）也能立刻随机化流光
  function scan(root) {
    if (!root || root.nodeType !== 1) { return; }
    if (root.matches && root.matches('.qcard, .hero, .card')) { randomSpin(root); }
    if (root.querySelectorAll) {
      root.querySelectorAll('.qcard, .hero, .card').forEach(randomSpin);
    }
  }
  function init() {
    document.querySelectorAll('.qcard, .hero, .card').forEach(randomSpin);
    new MutationObserver(function (ms) {
      ms.forEach(function (m) { m.addedNodes && m.addedNodes.forEach(scan); });
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); }
  else { init(); }
})();
