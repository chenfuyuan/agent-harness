/**
 * Bespoke Prototype Non-Intrusive Dev Overlay (Shadow DOM isolated)
 *
 * 用法：在原型页面定义 window.PROTO_SPEC = { ... } 后，于 </body> 前引入本脚本（如 `<script src="./dev-overlay.js"></script>`）：
 *   window.PROTO_SPEC = {
 *     title: '合约交易终端',
 *     views: [{ id: 'trade', label: '交易主屏' }, { id: 'review', label: '复盘账本' }], // 单视图应用可省略
 *     states: [
 *       { id: 'normal', label: '正常持仓' },
 *       { id: 'empty', label: '空仓初始态' },
 *       { id: 'danger', label: '止损未挂告警' }
 *     ],
 *     specs: [
 *       { kind: 'contract', text: '<OrderTicket> 未填止损价时，开仓主按钮置灰不可点并实时显示预计亏损金额' },
 *       { kind: 'mock', text: 'K线与盘口深度为静态模拟数据，Build 阶段接入欧易公开 WebSocket' },
 *       { kind: 'dev', text: '具体的强平维持保证金率档位由开发阶段按交易所接口实测为准' }
 *     ],
 *     onChange: ({ view, state }) => { ... } // 可选回调；同时自动在 document.documentElement 设置 data-proto-view / data-proto-state
 *   };
 *
 * 快捷键：Alt + D 隐藏/呼出悬浮胶囊（截图或沉浸预览时零遮挡）。
 */
(function () {
  const cfg = window.PROTO_SPEC || {};
  const views = Array.isArray(cfg.views) && cfg.views.length ? cfg.views : [{ id: 'default', label: '主视图' }];
  const states = Array.isArray(cfg.states) && cfg.states.length ? cfg.states : [{ id: 'default', label: '默认状态' }];
  const specs = Array.isArray(cfg.specs) ? cfg.specs : [];

  const params = new URLSearchParams(location.hash.includes('?') ? location.hash.split('?')[1] : location.search);
  let curView = params.get('view') || views[0].id;
  let curState = params.get('state') || states[0].id;
  let drawerOpen = false;
  let hidden = params.get('clean') === '1';

  function ensureDocumentHiddenRule() {
    if (document.getElementById('__proto_hidden_style__')) return;
    const docStyle = document.createElement('style');
    docStyle.id = '__proto_hidden_style__';
    // 确保显式声明了 display: flex / grid 的容器在被状态切换设为 [hidden] 时能可靠隐藏
    docStyle.textContent = '[data-when-view][hidden], [data-when-state][hidden] { display: none !important; }';
    (document.head || document.documentElement).appendChild(docStyle);
  }

  function applyDomAttributes() {
    document.documentElement.setAttribute('data-proto-view', curView);
    document.documentElement.setAttribute('data-proto-state', curState);

    // 联合求交集：同时声明 data-when-view 与 data-when-state 的元素需两者同时匹配才显示
    document.querySelectorAll('[data-when-view], [data-when-state]').forEach((el) => {
      const vAttr = el.getAttribute('data-when-view');
      const sAttr = el.getAttribute('data-when-state');
      const vOk = !vAttr || vAttr.split(',').map((s) => s.trim()).filter(Boolean).includes(curView);
      const sOk = !sAttr || sAttr.split(',').map((s) => s.trim()).filter(Boolean).includes(curState);
      el.hidden = !(vOk && sOk);
    });

    if (typeof cfg.onChange === 'function') {
      cfg.onChange({ view: curView, state: curState });
    }
    window.dispatchEvent(new CustomEvent('proto:change', { detail: { view: curView, state: curState } }));
  }

  const host = document.createElement('div');
  host.id = '__proto_dev_overlay__';
  const shadow = host.attachShadow({ mode: 'open' });

  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; position: fixed; right: 16px; bottom: 16px; z-index: 2147483640; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 12px; color: #e5e7eb; }
    .wrap { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
    .wrap[hidden] { display: none !important; }
    .pill { display: inline-flex; align-items: center; gap: 6px; background: rgba(15, 17, 21, 0.88); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.14); border-radius: 999px; padding: 5px 10px; box-shadow: 0 8px 24px rgba(0,0,0,0.35); }
    .tag { font-weight: 600; color: #9ca3af; padding: 0 4px; letter-spacing: 0.02em; }
    .sep { width: 1px; height: 12px; background: rgba(255,255,255,0.14); }
    button { all: unset; cursor: pointer; padding: 3px 8px; border-radius: 999px; color: #d1d5db; transition: background 0.15s, color 0.15s; white-space: nowrap; }
    button:hover { background: rgba(255,255,255,0.1); color: #fff; }
    button.active { background: #f3f4f6; color: #0f1115; font-weight: 600; }
    .drawer { width: min(380px, calc(100vw - 32px)); max-height: 52vh; overflow-y: auto; background: rgba(15, 17, 21, 0.94); backdrop-filter: blur(14px); border: 1px solid rgba(255,255,255,0.14); border-radius: 12px; padding: 12px 14px; box-shadow: 0 12px 32px rgba(0,0,0,0.45); line-height: 1.5; }
    .drawer h4 { margin: 0 0 8px; font-size: 12px; color: #fff; display: flex; justify-content: space-between; gap: 8px; }
    .spec-item { padding: 6px 0; border-top: 1px solid rgba(255,255,255,0.08); display: flex; gap: 8px; align-items: flex-start; }
    .badge { flex-shrink: 0; font-size: 10px; padding: 1px 6px; border-radius: 4px; font-weight: 600; }
    .b-contract { background: rgba(16, 185, 129, 0.2); color: #6ee7b7; }
    .b-mock { background: rgba(245, 158, 11, 0.2); color: #fcd34d; }
    .b-dev { background: rgba(99, 102, 241, 0.2); color: #a5b4fc; }
    .hint { color: #6b7280; font-size: 11px; flex-shrink: 0; }
  `;
  shadow.appendChild(style);

  const wrap = document.createElement('div');
  wrap.className = 'wrap';
  shadow.appendChild(wrap);

  const KIND_LABEL = {
    contract: ['交互契约', 'b-contract'],
    mock: ['模拟数据', 'b-mock'],
    dev: ['开发自决', 'b-dev'],
  };

  function render() {
    wrap.hidden = hidden;
    wrap.replaceChildren();

    if (drawerOpen && specs.length) {
      const d = document.createElement('div');
      d.className = 'drawer';

      const h4 = document.createElement('h4');
      const titleSpan = document.createElement('span');
      titleSpan.textContent = cfg.title || '原型设计批注';
      const hintSpan = document.createElement('span');
      hintSpan.className = 'hint';
      hintSpan.textContent = 'Alt+D 隐藏';
      h4.append(titleSpan, hintSpan);
      d.appendChild(h4);

      specs.forEach((s) => {
        const [lbl, cls] = KIND_LABEL[s.kind] || ['说明', 'b-dev'];
        const row = document.createElement('div');
        row.className = 'spec-item';
        const badge = document.createElement('span');
        badge.className = `badge ${cls}`;
        badge.textContent = lbl;
        const txt = document.createElement('span');
        txt.textContent = String(s.text ?? '');
        row.append(badge, txt);
        d.appendChild(row);
      });

      wrap.appendChild(d);
    }

    const pill = document.createElement('div');
    pill.className = 'pill';

    if (views.length > 1) {
      const vt = document.createElement('span');
      vt.className = 'tag';
      vt.textContent = '视图';
      pill.appendChild(vt);
      views.forEach((v) => {
        const b = document.createElement('button');
        b.textContent = v.label;
        if (v.id === curView) b.className = 'active';
        b.onclick = () => { curView = v.id; applyDomAttributes(); render(); };
        pill.appendChild(b);
      });
      const sep = document.createElement('span');
      sep.className = 'sep';
      pill.appendChild(sep);
    }

    const st = document.createElement('span');
    st.className = 'tag';
    st.textContent = '状态';
    pill.appendChild(st);
    states.forEach((s) => {
      const b = document.createElement('button');
      b.textContent = s.label;
      if (s.id === curState) b.className = 'active';
      b.onclick = () => { curState = s.id; applyDomAttributes(); render(); };
      pill.appendChild(b);
    });

    if (specs.length) {
      const sep2 = document.createElement('span');
      sep2.className = 'sep';
      pill.appendChild(sep2);
      const sb = document.createElement('button');
      sb.textContent = drawerOpen ? '收起说明' : `说明(${specs.length})`;
      if (drawerOpen) sb.className = 'active';
      sb.onclick = () => { drawerOpen = !drawerOpen; render(); };
      pill.appendChild(sb);
    }

    wrap.appendChild(pill);
  }

  window.addEventListener('keydown', (e) => {
    // 用物理键位 e.code 判断：macOS 上 Option+D 的 e.key 是 '∂' 而不是 'd'
    if (e.altKey && e.code === 'KeyD') {
      e.preventDefault();
      hidden = !hidden;
      render();
    }
  });

  function mount() {
    ensureDocumentHiddenRule();
    if (!document.body.contains(host)) document.body.appendChild(host);
    applyDomAttributes();
    render();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }

  window.__DEV_OVERLAY__ = {
    getMatrix: () => ({ views: views.map((v) => v.id), states: states.map((s) => s.id), specs }),
    setViewState: (v, s) => {
      if (v) curView = v;
      if (s) curState = s;
      applyDomAttributes();
      render();
    },
    setHidden: (val) => { hidden = !!val; render(); },
    setDrawerOpen: (val) => { drawerOpen = !!val; render(); },
  };
})();
