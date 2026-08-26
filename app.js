/* Claimifi.biz — analyzer logic, shared by the landing page (/) and /app.
   Every hook is optional, so the script is inert on a page that omits the widget. */
(function () {
  'use strict';

  // The server allows ~120s for Grok plus transcript time. The client budget must
  // exceed it, or real results get discarded moments before they arrive.
  var REQUEST_TIMEOUT_MS = 180000;

  var $ = function (id) { return document.getElementById(id); };
  var input = $('videoInput');
  var results = $('results');
  var btn = $('analyzeBtn');

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // "Unsupported" contains "supported", so it must be tested first.
  function verdictKey(v) {
    var s = String(v || '').toLowerCase();
    if (s.indexOf('unsupported') > -1) return 'unsupported';
    if (s.indexOf('supported') > -1) return 'supported';
    if (s.indexOf('mixed') > -1) return 'mixed';
    return 'insufficient';
  }

  /* ---------------- Status badge ---------------- */

  function setStatus(mode, text) {
    var el = $('status');
    if (!el) return;
    el.setAttribute('data-mode', mode);
    var t = $('statusText');
    if (t) t.textContent = text;
  }

  fetch('/api/config', { cache: 'no-store' })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (cfg) { setStatus(cfg.live ? 'live' : 'demo', cfg.live ? 'Live' : 'Demo mode'); })
    .catch(function () { setStatus('demo', 'Demo mode'); });

  if (!input || !results || !btn) return;

  /* ---------------- Rendering ---------------- */

  function shell(pillClass, pillText, body, extra) {
    results.innerHTML =
      '<div class="panel">' +
        '<div class="panel-head">' +
          '<h2>Analysis</h2>' +
          '<span class="pill ' + pillClass + '">' + esc(pillText) + '</span>' +
          (extra || '') +
        '</div>' + body +
      '</div>';
  }

  function renderLoading() {
    shell('busy', 'Working',
      '<div class="loading">' +
        '<div class="spinner" role="presentation"></div>' +
        '<div class="steps">' +
          '<div class="step" id="s0"><span class="box"></span>Fetching the transcript</div>' +
          '<div class="step" id="s1"><span class="box"></span>Extracting historical claims</div>' +
          '<div class="step" id="s2"><span class="box"></span>Checking against trusted sources</div>' +
          '<div class="step" id="s3"><span class="box"></span>Writing sourced verdicts</div>' +
        '</div>' +
        '<div class="elapsed" id="elapsed">0s elapsed</div>' +
      '</div>');
  }

  function renderError(msg, retryable) {
    shell('error', 'Error',
      '<div class="panel-body">' +
        '<div class="label">Could not complete the analysis</div>' +
        '<p style="color:var(--text-2)">' + esc(msg) + '</p>' +
        (retryable ? '<button class="ghost" type="button" id="retryBtn" style="margin-top:16px;margin-left:0">Try again</button>' : '') +
      '</div>');
    var r = $('retryBtn');
    if (r) r.addEventListener('click', run);
  }

  function tagLinks(list) {
    return (Array.isArray(list) ? list : []).map(function (s) {
      return '<a class="tag" href="https://' + esc(s) + '" target="_blank" rel="noopener nofollow">' + esc(s) + '</a>';
    }).join('');
  }

  function renderAnalysis(data, mode) {
    var claims = Array.isArray(data.claims) ? data.claims : [];

    var counts = { supported: 0, mixed: 0, unsupported: 0, insufficient: 0 };
    claims.forEach(function (c) { counts[verdictKey(c.verdict)]++; });

    var tally = [
      ['supported', 'Supported'], ['mixed', 'Mixed'],
      ['unsupported', 'Unsupported'], ['insufficient', 'Insufficient']
    ].map(function (d) {
      return '<div class="tally-item" data-v="' + d[0] + '">' +
               '<div class="n">' + counts[d[0]] + '</div><div class="t">' + d[1] + '</div>' +
             '</div>';
    }).join('');

    var claimsHtml = claims.map(function (c, i) {
      var k = verdictKey(c.verdict);
      var srcs = tagLinks(c.sources);
      return '<div class="claim" data-v="' + k + '">' +
               '<div class="claim-top">' +
                 '<span class="claim-n">' + (i + 1) + '</span>' +
                 '<span class="claim-text">' + esc(c.claim) + '</span>' +
               '</div>' +
               '<span class="verdict" data-v="' + k + '">' + esc(c.verdict) + '</span>' +
               '<div class="claim-why">' + esc(c.explanation) + '</div>' +
               (srcs ? '<div class="tags">' + srcs + '</div>' : '') +
             '</div>';
    }).join('');

    var used = tagLinks(data.sources_used);
    var pill = mode === 'demo' ? ['demo', 'Demo'] : ['done', 'Complete'];

    shell(pill[0], pill[1],
      '<div class="panel-body">' +
        '<div class="label">Video</div>' +
        '<p style="font-weight:500">' + esc(data.video_title || 'Unknown') + '</p>' +
        (data.video_id && data.video_id !== 'demo'
          ? '<p style="font-size:.82rem;color:var(--text-3);margin-top:4px">ID: ' + esc(data.video_id) + '</p>' : '') +
      '</div>' +
      '<div class="panel-body">' +
        '<div class="label">Verdict breakdown</div><div class="tally">' + tally + '</div>' +
      '</div>' +
      '<div class="panel-body">' +
        '<div class="label">' + claims.length + ' claim' + (claims.length === 1 ? '' : 's') + ' checked</div>' +
        (claimsHtml || '<p style="color:var(--text-2)">No distinct claims were extracted from this video.</p>') +
      '</div>' +
      '<div class="panel-body">' +
        '<div class="label">Overall assessment</div>' +
        '<p style="color:var(--text-2)">' + esc(data.overall_assessment) + '</p>' +
      '</div>' +
      '<div class="panel-body">' +
        '<div class="label">Sources consulted</div>' +
        '<div class="tags">' + (used || '<span style="color:var(--text-3)">None reported</span>') + '</div>' +
        '<p style="font-size:.8rem;color:var(--text-3);margin-top:16px">' + esc(data.note || '') + '</p>' +
      '</div>',
      '<button class="ghost" type="button" id="copyBtn">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<rect x="9" y="9" width="13" height="13" rx="2"/>' +
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>Copy report</button>');

    var copyBtn = $('copyBtn');
    if (copyBtn) {
      copyBtn.addEventListener('click', function () {
        var lines = ['Claimifi.biz — ' + (data.video_title || ''), ''];
        claims.forEach(function (c, i) {
          lines.push((i + 1) + '. ' + c.claim);
          lines.push('   Verdict: ' + c.verdict);
          lines.push('   ' + c.explanation);
          if (c.sources && c.sources.length) lines.push('   Sources: ' + c.sources.join(', '));
          lines.push('');
        });
        lines.push('Overall: ' + data.overall_assessment);
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(lines.join('\n')).then(function () {
            copyBtn.textContent = 'Copied';
            setTimeout(function () { copyBtn.textContent = 'Copy report'; }, 1800);
          }, function () {});
        }
      });
    }
  }

  /* ---------------- Demo fallback ---------------- */

  function demoData(q) {
    var isUrl = /youtube\.com|youtu\.be/.test(q);
    return {
      video_title: isUrl ? 'YouTube video (demo)' : '"' + q + '"',
      video_id: isUrl ? 'demo' : null,
      claims: [
        {
          claim: 'The video presents a single chronological sequence of events as settled fact.',
          verdict: 'Mixed',
          explanation: 'Popular history videos tend to compress complex timelines. Scholarship indexed on cambridge.org and historians.org generally shows more overlapping causes and regional variation than one linear narrative allows.',
          sources: ['cambridge.org', 'historians.org', 'jstor.org']
        },
        {
          claim: 'Key figures are given clear motives that fully explain the outcome.',
          verdict: 'Supported',
          explanation: 'Primary documents held at archives.gov and monographs from major university presses do record the stated motives of prominent actors, though historians continue to debate how decisive those motives actually were.',
          sources: ['archives.gov', 'hup.harvard.edu', 'yalebooks.yale.edu']
        },
        {
          claim: 'A precise casualty figure is asserted for the event.',
          verdict: 'Insufficient Evidence',
          explanation: 'Exact numbers for ancient and early-modern events are rarely recoverable with confidence. Collections at loc.gov and journals on academic.oup.com typically present ranges and note the limits of surviving evidence.',
          sources: ['loc.gov', 'academic.oup.com', 'jstor.org']
        }
      ],
      overall_assessment: 'This is a sample analysis generated in your browser. In live mode Claimifi.biz reads the real transcript, identifies the actual claims, and checks each one against the 32 trusted sources. The structure shown here matches what the live version returns.',
      sources_used: ['historians.org', 'cambridge.org', 'archives.gov', 'loc.gov', 'jstor.org', 'hup.harvard.edu'],
      note: 'Demo mode — no analysis was performed on a real transcript.'
    };
  }

  /* ---------------- Run ---------------- */

  var running = false;

  function run() {
    if (running) return;
    var q = input.value.trim();
    if (!q) { input.focus(); return; }

    running = true;
    btn.disabled = true;
    renderLoading();
    results.scrollIntoView({ block: 'start' });

    // Progress affordances on a rough schedule, not real server milestones.
    var timers = [0, 6000, 14000, 26000].map(function (ms, i) {
      return setTimeout(function () {
        if (i > 0) { var p = $('s' + (i - 1)); if (p) { p.classList.remove('on'); p.classList.add('ok'); } }
        var el = $('s' + i); if (el) el.classList.add('on');
      }, ms);
    });

    var t0 = Date.now();
    var tick = setInterval(function () {
      var el = $('elapsed');
      if (el) el.textContent = Math.round((Date.now() - t0) / 1000) + 's elapsed';
    }, 1000);

    var cleanup = function () {
      running = false;
      btn.disabled = false;
      timers.forEach(clearTimeout);
      clearInterval(tick);
    };

    var ctrl = new AbortController();
    var killer = setTimeout(function () { ctrl.abort(); }, REQUEST_TIMEOUT_MS);
    var isUrl = /youtube\.com|youtu\.be/.test(q);

    fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(isUrl ? { url: q } : { title: q }),
      signal: ctrl.signal
    })
    .then(function (res) {
      clearTimeout(killer);
      if (res.ok) {
        return res.json().then(function (d) { setStatus('live', 'Live'); renderAnalysis(d, 'live'); });
      }
      return res.json().catch(function () { return {}; }).then(function (err) {
        // 503 means the server is healthy but has no Grok key. That is the one
        // case where showing the sample analysis is honest.
        if (res.status === 503) {
          setStatus('demo', 'Demo mode');
          renderAnalysis(demoData(q), 'demo');
          return;
        }
        renderError(
          typeof err.detail === 'string' ? err.detail : 'The server returned an error (' + res.status + ').',
          res.status >= 500 || res.status === 429
        );
      });
    })
    .catch(function (e) {
      clearTimeout(killer);
      if (e && e.name === 'AbortError') {
        renderError('The analysis ran past three minutes and was stopped. Try a shorter video.', true);
      } else {
        // Genuine network failure: offline, or the server is unreachable.
        setStatus('demo', 'Demo mode');
        renderAnalysis(demoData(q), 'demo');
      }
    })
    .then(cleanup, cleanup);
  }

  btn.addEventListener('click', run);
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') run(); });

  Array.prototype.forEach.call(document.querySelectorAll('.chip'), function (c) {
    c.addEventListener('click', function () { input.value = c.textContent; run(); });
  });

  // Landing-page CTAs that scroll back to the analyzer.
  Array.prototype.forEach.call(document.querySelectorAll('[data-focus-search]'), function (el) {
    el.addEventListener('click', function (e) {
      e.preventDefault();
      window.scrollTo({ top: 0 });
      input.focus();
    });
  });

  // Deep link: /app?q=... prefills and runs immediately.
  try {
    var q0 = new URLSearchParams(window.location.search).get('q');
    if (q0) { input.value = q0; run(); }
  } catch (e) { /* URLSearchParams unavailable */ }
})();
