/* Claimifi.biz — analyzer logic.
   Loaded by both pages, but only /app carries the widget: on the landing page
   everything below the hook check is skipped and only the status badge runs. */
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

  // The server accepts a bare 11-character id as a URL. Without this the page
  // sent it as a *title* instead, and the user got an analysis of a meaningless
  // string presented as a completed check.
  //
  // The digit/underscore/hyphen requirement matters: "Renaissance",
  // "Reformation" and "Charlemagne" are all exactly 11 letters, and routing
  // those as video ids broke the title-only path for common one-word topics.
  // Kept identical to _VIDEO_ID_PATTERNS[1] in main.py.
  function looksLikeVideo(q) {
    return /youtube\.com|youtu\.be|youtube-nocookie\.com/.test(q) ||
           /^(?=[a-zA-Z0-9_-]{11}$)[a-zA-Z]*[0-9_-][a-zA-Z0-9_-]*$/.test(q);
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

  // Short, discrete announcements for screen readers. The results panel is
  // rewritten wholesale on every render and carries a per-second timer, so it
  // is the wrong element to make a live region.
  function announce(text) {
    var el = $('srStatus');
    if (el) el.textContent = text;
  }

  function setBusy(on) {
    if (results) results.setAttribute('aria-busy', on ? 'true' : 'false');
  }

  function setStatus(mode, text) {
    var el = $('status');
    if (!el) return;
    el.setAttribute('data-mode', mode);
    var t = $('statusText');
    if (t) t.textContent = text;
  }

  fetch('/api/config', { cache: 'no-store' })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (cfg) { setStatus(cfg.live ? 'live' : 'demo', cfg.live ? 'Live' : 'Not configured'); })
    .catch(function () { setStatus('demo', 'Unavailable'); });

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
        '<div class="elapsed" id="elapsed" aria-hidden="true">0s elapsed</div>' +
      '</div>');
  }

  // FastAPI returns `detail` as a string for our own HTTPExceptions, but as a
  // list of error objects for anything pydantic rejects (a title over 300
  // chars, a malformed body). Assuming a string turned every one of those into
  // an opaque "The server returned an error (422)."
  function detailText(detail, status) {
    if (typeof detail === 'string' && detail) return detail;
    if (Array.isArray(detail) && detail.length) {
      var parts = detail.map(function (d) {
        return d && typeof d.msg === 'string' ? d.msg : null;
      }).filter(Boolean);
      if (parts.length) return parts.join('; ') + '.';
    }
    return 'The server returned an error (' + status + ').';
  }

  function renderError(msg, retryable) {
    announce('Analysis failed. ' + msg);
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

  function renderAnalysis(data) {
    var claims = Array.isArray(data.claims) ? data.claims : [];
    var titleOnly = data.basis === 'title';

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
    // A title-only run never read the video. It has to be visibly different
    // from one that did, or the page presents guesswork as a transcript check.
    var pill = titleOnly ? ['demo', 'Title only'] : ['done', 'Complete'];

    shell(pill[0], pill[1],
      (titleOnly
        ? '<div class="panel-body notice">' +
            '<p>No transcript was read. This covers the claims a video with this ' +
            'title typically makes, not what this video actually says.</p>' +
          '</div>'
        : '') +
      '<div class="panel-body">' +
        '<div class="label">' + (titleOnly ? 'Title' : 'Video') + '</div>' +
        '<p style="font-weight:500">' + esc(data.video_title || 'Unknown') + '</p>' +
        (data.video_id
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
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>' +
        '<span id="copyLabel">Copy report</span></button>');

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

        // navigator.clipboard is undefined on any non-HTTPS origin, and the
        // write can be rejected outright. Silently doing nothing reads as a
        // broken button, so both outcomes get a label.
        var flash = function (text) {
          var label = $('copyLabel');
          if (!label) return;
          label.textContent = text;
          setTimeout(function () { label.textContent = 'Copy report'; }, 1800);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(lines.join('\n')).then(
            function () { flash('Copied'); },
            function () { flash('Copy failed'); }
          );
        } else {
          flash('Copy unavailable');
        }
      });
    }
  }

  /* ---------------- Run ---------------- */

  var running = false;

  function run() {
    if (running) return;
    var q = input.value.trim();
    if (!q) { input.focus(); return; }

    running = true;
    btn.disabled = true;
    setBusy(true);
    announce('Analyzing. This usually takes about a minute.');
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
      setBusy(false);
      timers.forEach(clearTimeout);
      clearInterval(tick);
    };

    var ctrl = new AbortController();
    var killer = setTimeout(function () { ctrl.abort(); }, REQUEST_TIMEOUT_MS);

    fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(looksLikeVideo(q) ? { url: q } : { title: q }),
      signal: ctrl.signal
    })
    .then(function (res) {
      clearTimeout(killer);
      if (res.ok) {
        return res.json().then(function (d) {
          setStatus('live', 'Live');
          renderAnalysis(d);
          announce((d.basis === 'title'
            ? 'Title-only analysis complete, no transcript was read. '
            : 'Analysis complete. ') + ((d.claims || []).length) + ' claims checked.');
        });
      }
      // A non-JSON body is normal for an error served by an edge proxy rather
      // than by this app, so parse defensively and fall back to the status.
      return res.json().catch(function () { return {}; }).then(function (err) {
        if (res.status === 503 && err.reason === 'no_api_key') {
          // The server is up but has no Grok key, so nothing can be analysed.
          // It is still reported as an error: showing sample verdicts here
          // would be indistinguishable from a real result, on a page whose
          // whole purpose is telling those two apart.
          setStatus('demo', 'Not configured');
        }
        renderError(
          detailText(err.detail, res.status),
          res.status >= 500 || res.status === 429
        );
      });
    })
    .catch(function (e) {
      clearTimeout(killer);
      if (e && e.name === 'AbortError') {
        renderError('The analysis ran past three minutes and was stopped. Try a shorter video.', true);
      } else {
        // Offline, DNS, a dropped connection, or a reply this page could not
        // read. Every one of them is a failure to report, never a cue to
        // invent an analysis: this is a fact-checker.
        console.error('Analysis request failed:', e);
        renderError('The connection was interrupted before the analysis came back. Please try again.', true);
      }
    })
    .then(cleanup, cleanup);
  }

  btn.addEventListener('click', run);
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') run(); });

  Array.prototype.forEach.call(document.querySelectorAll('.chip'), function (c) {
    c.addEventListener('click', function () {
      // Guarded: otherwise the box shows one query while the panel below still
      // shows the results of another.
      if (running) return;
      input.value = c.textContent;
      run();
    });
  });


  // Deep link: /app?q=... prefills the box. It deliberately does not run on
  // its own — an analysis costs an API call and a slice of the visitor's
  // quota, and a link should not be able to spend either without a click.
  try {
    var q0 = new URLSearchParams(window.location.search).get('q');
    if (q0) {
      // 300 is the server's title limit; a pasted URL may legitimately be
      // longer, so only the title path gets clipped.
      input.value = looksLikeVideo(q0) ? q0.slice(0, 2000) : q0.slice(0, 300);
      input.focus();
    }
  } catch (e) { /* URLSearchParams unavailable */ }
})();
