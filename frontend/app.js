(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const money = new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' });
  const bucketNames = { daily: '日常开支', monthly: '月度开支', large: '大型开支', income: '收入' };
  const categoryChoices = ['日常开支', '月度开支', '大型开支', '餐饮', '交通', '购物', '医疗', '娱乐', '收入'];
  const state = {
    profile: {}, settings: {}, transactions: [], items: [], panels: {},
    llmConfig: {}, conversations: [], activeConversationId: null, messages: [],
  };
  const now = new Date();
  const currentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;

  async function request(path, options = {}) {
    const response = await fetch(`/api${path}`, {
      method: options.method || 'GET',
      headers: options.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
      body: options.body === undefined ? undefined :
        (options.body instanceof FormData ? options.body : JSON.stringify(options.body)),
    });
    let data = null;
    try { data = await response.json(); } catch (error) { /* Empty response. */ }
    if (!response.ok) {
      const failure = new Error((data && data.error) || `请求失败（HTTP ${response.status}）`);
      failure.status = response.status;
      throw failure;
    }
    return data;
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function formatMoney(value) { return money.format(Number(value) || 0); }

  function toast(message, isError = false) {
    const element = document.createElement('div');
    element.className = `toast${isError ? ' error' : ''}`;
    element.textContent = message;
    $('#toast-root').append(element);
    setTimeout(() => element.remove(), 3600);
  }

  function selectedMonth() { return $('#month-select').value || null; }

  function transactionsForMonth() {
    const month = selectedMonth();
    if (!month) return state.transactions;
    return state.transactions.filter(transaction => transaction.date.startsWith(month));
  }

  function transactionQuery() {
    const input = $('#transaction-search');
    return input ? (input.value || '').trim().toLowerCase() : '';
  }

  function visibleTransactions() {
    const query = transactionQuery();
    const rows = transactionsForMonth();
    if (!query) return rows;
    return rows.filter(transaction =>
      [transaction.merchant, transaction.note, transaction.category, transaction.source]
        .filter(Boolean).join(' ').toLowerCase().includes(query));
  }

  function renderProfile() {
    $('#profile-name').textContent = state.profile.displayName || '我的空间';
    $('#profile-avatar').textContent = state.profile.avatar || '🌱';
    const form = $('#profile-form');
    form.elements.displayName.value = state.profile.displayName || '我的空间';
    form.elements.avatar.value = state.profile.avatar || '🌱';
  }

  function renderSummary(transactions) {
    const prefix = selectedMonth() ? '本月' : '累计';
    $('#summary-net-label').textContent = `${prefix}结余`;
    $('#summary-income-label').innerHTML = `<i class="dot income-dot"></i>${prefix}收入`;
    $('#summary-expense-label').innerHTML = `<i class="dot expense-dot"></i>${prefix}支出`;
    $('#breakdown-eyebrow').textContent = `${prefix}消费`;
    const income = transactions.filter(item => item.direction === 'income');
    const expenses = transactions.filter(item => item.direction === 'expense');
    const incomeTotal = income.reduce((sum, item) => sum + Number(item.amount), 0);
    const expenseTotal = expenses.reduce((sum, item) => sum + Number(item.amount), 0);
    $('#summary-net').textContent = formatMoney(incomeTotal - expenseTotal);
    $('#summary-income').textContent = formatMoney(incomeTotal);
    $('#summary-expense').textContent = formatMoney(expenseTotal);
    $('#income-count').textContent = income.length;
    $('#expense-count').textContent = expenses.length;

    const buckets = ['daily', 'monthly', 'large'];
    const totals = buckets.map(bucket => expenses
      .filter(item => item.bucket === bucket)
      .reduce((sum, item) => sum + Number(item.amount), 0));
    const max = Math.max(...totals, 0);
    $('#breakdown').innerHTML = totals.every(total => !total)
      ? '<p class="empty-note">导入账单后，这里会显示日常、月度与大型开支。</p>'
      : buckets.map((bucket, index) => {
        const details = expenses.filter(item => item.bucket === bucket);
        const categoryTotals = {};
        details.forEach(item => { categoryTotals[item.category] = (categoryTotals[item.category] || 0) + Number(item.amount); });
        const topCategories = Object.entries(categoryTotals).sort((a, b) => b[1] - a[1]).slice(0, 2)
          .map(([name]) => name).join('、');
        const width = max ? Math.round(totals[index] / max * 100) : 0;
        return `<div class="breakdown-row">
          <span class="breakdown-label" title="${escapeHtml(topCategories || bucketNames[bucket])}">${bucketNames[bucket]}</span>
          <div class="progress-track" aria-label="${bucketNames[bucket]}占比 ${width}%"><div class="progress-fill" style="width:${width}%"></div></div>
          <strong class="breakdown-value">${formatMoney(totals[index])}</strong>
        </div>`;
      }).join('');
  }

  function renderDailyChart(transactions) {
    const monthValue = selectedMonth();
    if (!monthValue) {
      $('#daily-chart').innerHTML = '<p class="empty-note">选择月份后查看每日收支</p>';
      return;
    }
    const [year, month] = monthValue.split('-').map(Number);
    const daysInMonth = new Date(year, month, 0).getDate();
    const daily = new Map();
    transactions.forEach(item => {
      const day = Number(item.date.slice(8, 10));
      const entry = daily.get(day) || { income: 0, expense: 0 };
      entry[item.direction] += Number(item.amount);
      daily.set(day, entry);
    });
    const max = Math.max(1, ...[...daily.values()].flatMap(value => [value.income, value.expense]));
    $('#daily-chart').innerHTML = Array.from({ length: daysInMonth }, (_, index) => {
      const day = index + 1;
      const values = daily.get(day) || { income: 0, expense: 0 };
      const incomeHeight = values.income ? Math.max(3, Math.round(values.income / max * 88)) : 2;
      const expenseHeight = values.expense ? Math.max(3, Math.round(values.expense / max * 88)) : 2;
      return `<div class="day-column" title="${escapeHtml(month)}-${String(day).padStart(2, '0')} 收入 ${formatMoney(values.income)} · 支出 ${formatMoney(values.expense)}">
        <div class="day-bars"><span class="day-bar income" style="height:${incomeHeight}px"></span><span class="day-bar expense" style="height:${expenseHeight}px"></span></div>
        <span class="day-label">${day}</span>
      </div>`;
    }).join('');
  }

  function categoryOptions(transaction) {
    const choices = [...new Set([...categoryChoices, ...state.settings.rules.map(rule => rule.category), transaction.category])];
    return choices.map(category => `<option value="${escapeHtml(category)}"${category === transaction.category ? ' selected' : ''}>${escapeHtml(category)}</option>`).join('') +
      '<option value="__custom__">＋ 自定义分类…</option>';
  }

  function transactionTable(transactions) {
    if (!transactions.length) return '<p class="empty-note">这个月还没有流水。导入微信 / 支付宝账单后，记录会显示在这里。</p>';
    const rows = transactions.map(transaction => {
      const title = transaction.merchant || transaction.category || '账单记录';
      const secondary = [transaction.note, transaction.source].filter(Boolean).join(' · ');
      return `<tr>
        <td class="transaction-date">${escapeHtml(transaction.date)}</td>
        <td><div class="transaction-merchant" title="${escapeHtml(title)}">${escapeHtml(title)}</div><div class="transaction-note">${escapeHtml(secondary)}</div></td>
        <td><select class="category-select" data-id="${escapeHtml(transaction.id)}" aria-label="修改分类">${categoryOptions(transaction)}</select></td>
        <td class="transaction-source">${escapeHtml(bucketNames[transaction.bucket] || '')}</td>
        <td class="amount ${transaction.direction === 'income' ? 'income' : 'expense'}">${transaction.direction === 'income' ? '+' : '−'}${formatMoney(transaction.amount)}</td>
        <td class="transaction-actions">
          <button class="text-button edit-transaction" data-id="${escapeHtml(transaction.id)}" type="button">编辑</button>
          <button class="text-button danger delete-transaction" data-id="${escapeHtml(transaction.id)}" type="button">删除</button>
        </td>
      </tr>`;
    }).join('');
    return `<div class="table-scroll"><table class="transaction-table">
      <thead><tr><th>日期</th><th>交易对象 / 摘要</th><th>分类（可修改）</th><th>开支类型</th><th style="text-align:right">金额</th><th style="text-align:right">操作</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }

  function renderTransactions() {
    const transactions = transactionsForMonth();
    renderSummary(transactions);
    renderDailyChart(transactions);
    const sorted = [...visibleTransactions()].sort((a, b) => b.date.localeCompare(a.date));
    $('#recent-transactions').innerHTML = transactionTable(sorted.slice(0, 8));
    $('#all-transactions').innerHTML = transactionTable(sorted);
    $('#month-select-secondary').value = selectedMonth();
  }

  function renderRules() {
    const rules = state.settings.rules || [];
    $('#large-threshold').value = state.settings.largeThreshold || 1000;
    $('#rule-count').textContent = `${rules.length} 条`;
    $('#rules-list').innerHTML = rules.length ? rules.map(rule => `
      <div class="rule-row">
        <div class="rule-description">
          <span class="rule-keyword">${escapeHtml(rule.keyword)}</span>
          ${rule.amount === null ? '' : `<span class="rule-tag">金额 ¥${escapeHtml(rule.amount)}</span>`}
          <span class="rule-arrow">→</span><span class="rule-category">${escapeHtml(rule.category)}</span>
          <span class="rule-tag">${bucketNames[rule.bucket] || ''}</span>
        </div>
        <button class="icon-button delete-rule" data-id="${escapeHtml(rule.id)}" aria-label="删除规则">×</button>
      </div>`).join('') : '<p class="empty-note">还没有自定义规则。添加规则后，会优先于默认分类。</p>';
  }

  const assetTypes = { fund: '基金', stock: '股票', cash: '现金', other: '其他' };

  function renderAssets() {
    const total = state.items.reduce((sum, item) => sum + Number(item.value), 0);
    $('#assets-total').textContent = formatMoney(total);
    $('#assets-list').innerHTML = state.items.length ? state.items.map(item => {
      const cost = Number(item.quantity) * Number(item.costPrice);
      const profit = Number(item.value) - cost;
      return `<article class="asset-card">
        <div class="asset-card-top"><div><h3>${escapeHtml(item.name)}</h3><span class="asset-type">${assetTypes[item.type] || '其他'} · ${escapeHtml(item.quantity)} 份</span></div>
        <button class="icon-button delete-asset" data-id="${escapeHtml(item.id)}" aria-label="删除资产">×</button></div>
        <div class="asset-value">${formatMoney(item.value)}</div>
        <div class="asset-return">成本 ${formatMoney(cost)} · ${profit >= 0 ? '浮盈' : '浮亏'} ${formatMoney(Math.abs(profit))}</div>
      </article>`;
    }).join('') : '<section class="card empty-note">添加一项基金、股票或现金记录，开始整理你的个人资产。</section>';
  }

  function renderPanels() {
    const definitions = [
      ['stocks', 'panel-stocks', 'code', 'name', 'price'],
      ['currencies', 'panel-currencies', 'code', 'name', 'rate'],
      ['metals', 'panel-metals', 'code', 'name', 'price'],
    ];
    definitions.forEach(([key, target, codeKey, nameKey, valueKey]) => {
      const panel = state.panels[key] || {};
      const rows = panel.rows || [];
      $(`#${target}`).innerHTML = rows.length ? rows.map(row => `
        <div class="market-row"><span>${escapeHtml(row[nameKey] || row[codeKey])} <small>${escapeHtml(row[codeKey] || '')}</small></span>
        <span>${row[valueKey] == null ? '待接入' : escapeHtml(row[valueKey])}</span></div>`).join('') :
        '<p class="empty-note">接口已保留，行情服务暂未开放。</p>';
    });
  }

  function renderReminders() {
    const reminders = state.reminders || [];
    $('#reminders-list').innerHTML = reminders.map(item =>
      `<div class="reminder ${escapeHtml(item.level)}">
        <strong>${escapeHtml(item.title)}</strong>
        <span>${escapeHtml(item.text)}</span>
      </div>`).join('');
  }

  function conversationById(conversationId) {
    return (state.conversations || []).find(item => item.id === conversationId);
  }

  function renderConversationList() {
    const list = $('#conversation-list');
    const conversations = state.conversations || [];
    list.innerHTML = conversations.length
      ? conversations.map(conversation => `
        <button class="conversation-item${conversation.id === state.activeConversationId ? ' active' : ''}"
          data-id="${escapeHtml(conversation.id)}" type="button" title="${escapeHtml(conversation.title)}">
          <span>💬</span>
          <span class="conversation-title">${escapeHtml(conversation.title)}</span>
        </button>`).join('')
      : '<p class="empty-note">还没有对话</p>';
  }

  function renderChatWelcome() {
    $('#chat-messages').innerHTML = `
      <div class="chat-welcome">
        <h3>今天想了解什么？</h3>
        <p>答案都来自你导入的账单，并附来源依据。试试下面这些问题：</p>
        <div class="suggestion-list">
          <button class="suggestion" type="button" data-question="这个月花了多少？">这个月花了多少？</button>
          <button class="suggestion" type="button" data-question="上个月餐饮花了多少？">上个月餐饮花了多少？</button>
          <button class="suggestion" type="button" data-question="哪类支出最高？">哪类支出最高？</button>
        </div>
      </div>`;
  }

  function renderMessage(message) {
    if (message.role === 'user') {
      return `<div class="chat-row user"><div class="chat-bubble user-bubble">${escapeHtml(message.content)}</div></div>`;
    }
    if (message.pending) {
      return `<div class="chat-row assistant"><div class="chat-bubble assistant-bubble pending">正在思考…</div></div>`;
    }
    const evidence = (message.evidence || []).map(item =>
      `<li>${escapeHtml(item.date)} ${escapeHtml(item.merchant || item.category || '未命名')} ` +
      `${item.direction === 'income' ? '+' : '−'}${escapeHtml(formatMoney(item.amount))}</li>`).join('');
    const sourceLabel = message.source === 'llm'
      ? `由大模型生成（${escapeHtml(message.model || 'LLM')}）`
      : '由本地核算生成';
    return `<div class="chat-row assistant">
      <div class="chat-bubble assistant-bubble">
        <div class="chat-answer">${escapeHtml(message.content)}</div>
        <div class="qa-source">${sourceLabel}</div>
        ${evidence ? `<div class="qa-evidence-label">来源依据（${message.evidence.length} 笔）：</div><ul class="qa-evidence">${evidence}</ul>` : ''}
      </div>
    </div>`;
  }

  function renderMessages() {
    const box = $('#chat-messages');
    if (!(state.messages && state.messages.length)) {
      renderChatWelcome();
      return;
    }
    box.innerHTML = state.messages.map(renderMessage).join('');
    box.scrollTop = box.scrollHeight;
  }

  function setConversationTitle(conversation) {
    const element = $('#conversation-title');
    if (element) element.textContent = conversation ? conversation.title : '新对话';
  }

  async function openConversation(conversationId) {
    if (!conversationId) return;
    state.activeConversationId = conversationId;
    setConversationTitle(conversationById(conversationId));
    renderConversationList();
    try {
      const result = await request(`/conversations/${encodeURIComponent(conversationId)}`);
      state.messages = result.messages;
      renderMessages();
    } catch (error) {
      toast(error.message, true);
      state.messages = [];
      renderMessages();
    }
  }

  async function refreshConversations() {
    const result = await request('/conversations');
    state.conversations = result.conversations;
    renderConversationList();
  }

  async function newConversation() {
    try {
      const result = await request('/conversations', { method: 'POST', body: {} });
      state.conversations.unshift(result.conversation);
      state.activeConversationId = result.conversation.id;
      state.messages = [];
      setConversationTitle(result.conversation);
      renderConversationList();
      renderMessages();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function deleteCurrentConversation() {
    const conversationId = state.activeConversationId;
    if (!conversationId) return;
    if (!window.confirm('确定删除当前对话吗？此操作不可恢复。')) return;
    try {
      await request(`/conversations/${encodeURIComponent(conversationId)}`, { method: 'DELETE' });
      state.conversations = state.conversations.filter(item => item.id !== conversationId);
      if (state.conversations.length) {
        await openConversation(state.conversations[0].id);
      } else {
        await newConversation();
      }
    } catch (error) {
      toast(error.message, true);
    }
  }

  let sendingMessage = false;
  async function sendMessage() {
    const input = $('#chat-input');
    const question = (input.value || '').trim();
    if (!question || sendingMessage) return;
    if (!state.activeConversationId) {
      await newConversation();
    }
    sendingMessage = true;
    input.value = '';
    state.messages.push({ role: 'user', content: question });
    state.messages.push({ role: 'assistant', pending: true });
    renderMessages();
    try {
      const result = await request(
        `/conversations/${encodeURIComponent(state.activeConversationId)}/messages`,
        { method: 'POST', body: { question } },
      );
      state.messages = state.messages.filter(item => !item.pending);
      state.messages.push(result.message);
      renderMessages();
      await refreshConversations();
    } catch (error) {
      state.messages = state.messages.filter(item => !item.pending);
      state.messages.push({ role: 'assistant', content: `出错了：${error.message}`, source: 'local', evidence: [] });
      renderMessages();
    } finally {
      sendingMessage = false;
      input.focus();
    }
  }

  async function initAssistant() {
    renderConversationList();
    if (!(state.conversations && state.conversations.length)) {
      await newConversation();
      return;
    }
    await openConversation(state.conversations[0].id);
  }

  function renderQaMode() {
    const element = $('#qa-mode');
    if (!element) return;
    const config = state.llmConfig || {};
    element.textContent = config.enabled && config.hasApiKey
      ? `已接入大模型：${config.model}`
      : '当前为本地核算模式；可在左下角「大模型配置」中启用';
  }

  function renderLlmForm() {
    const form = $('#llm-form');
    if (!form) return;
    const config = state.llmConfig || {};
    form.elements.enabled.checked = !!config.enabled;
    form.elements.baseUrl.value = config.baseUrl || 'https://api.openai.com/v1';
    form.elements.model.value = config.model || 'gpt-4o-mini';
    form.elements.timeout.value = config.timeout || 20;
    form.elements.apiKey.value = '';
    form.elements.clearApiKey.checked = false;
    const status = $('#llm-status');
    if (status) {
      status.textContent = config.enabled && config.hasApiKey
        ? `已启用 · ${config.model}`
        : config.enabled ? '已启用 · 未保存密钥' : '未启用';
    }
  }

  async function reloadReminders() {
    try {
      const result = await request('/reminders');
      state.reminders = result.reminders;
      renderReminders();
    } catch (error) {
      $('#reminders-list').innerHTML = `<p class="empty-note">${escapeHtml(error.message)}</p>`;
    }
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $('#theme-select').value = theme;
  }

  function render() {
    renderProfile();
    renderTransactions();
    renderRules();
    renderAssets();
    renderPanels();
    renderReminders();
    renderQaMode();
    renderLlmForm();
  }

  async function reloadTransactions() {
    const result = await request('/transactions');
    state.transactions = result.transactions;
    renderTransactions();
  }

  async function saveSettings(changes) {
    state.settings = await request('/settings', { method: 'PUT', body: changes });
    renderRules();
    await reloadTransactions();
  }

  async function updateCategory(id, category) {
    const result = await request(`/transactions/${encodeURIComponent(id)}`, { method: 'PUT', body: { category } });
    const index = state.transactions.findIndex(item => item.id === id);
    if (index >= 0) state.transactions[index] = result.transaction;
    renderTransactions();
    toast('分类已更新');
  }

  let editingTransactionId = null;

  function openTransactionEditor(transactionId) {
    const transaction = state.transactions.find(item => item.id === transactionId);
    if (!transaction) return;
    editingTransactionId = transactionId;
    const form = $('#transaction-form');
    form.elements.date.value = transaction.date;
    form.elements.amount.value = transaction.amount;
    form.elements.direction.value = transaction.direction;
    form.elements.merchant.value = transaction.merchant || '';
    form.elements.note.value = transaction.note || '';
    form.elements.category.value = transaction.category || '';
    $('#transaction-dialog').showModal();
  }

  async function deleteTransaction(transactionId) {
    if (!window.confirm('确定删除这笔流水吗？此操作不可恢复。')) return;
    try {
      await request(`/transactions/${encodeURIComponent(transactionId)}`, { method: 'DELETE' });
      state.transactions = state.transactions.filter(item => item.id !== transactionId);
      renderTransactions();
      toast('流水已删除');
    } catch (error) {
      toast(error.message, true);
    }
  }

  function showView(view) {
    $$('.view').forEach(section => section.classList.toggle('hidden', section.id !== `view-${view}`));
    $$('.nav-link').forEach(button => button.classList.toggle('active', button.dataset.view === view));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  async function uploadFile(file) {
    if (!file) return;
    const feedback = $('#upload-feedback');
    feedback.className = 'feedback';
    feedback.textContent = `正在读取 ${file.name}…`;
    const form = new FormData();
    form.append('file', file);
    try {
      const platform = detectPlatform(file.name);
      const result = await request(`/bills/${platform}`, { method: 'POST', body: form });
      await reloadTransactions();
      feedback.textContent = `已处理 ${result.parsed} 笔：新增 ${result.imported} 笔，跳过重复 ${result.duplicates} 笔。`;
      toast(`账单已导入，新增 ${result.imported} 笔`);
    } catch (error) {
      feedback.className = 'feedback error';
      feedback.textContent = error.message;
      toast(error.message, true);
    } finally {
      $('#bill-file').value = '';
    }
  }

  function detectPlatform(filename) {
    const lower = String(filename || '').toLowerCase();
    if (lower.includes('支付宝') || lower.includes('alipay')) return 'alipay';
    if (lower.includes('银联') || lower.includes('unionpay') || lower.includes('云闪付')) return 'unionpay';
    return 'wechat';
  }

  function bindEvents() {
    $$('.nav-link').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
    $$('[data-go]').forEach(button => button.addEventListener('click', () => showView(button.dataset.go)));
    $('#month-select').addEventListener('change', renderTransactions);
    $('#month-select-secondary').addEventListener('change', event => {
      $('#month-select').value = event.target.value;
      renderTransactions();
    });
    $('#transaction-search').addEventListener('input', renderTransactions);
    document.addEventListener('click', event => {
      const edit = event.target.closest('.edit-transaction');
      if (edit) { openTransactionEditor(edit.dataset.id); return; }
      const remove = event.target.closest('.delete-transaction');
      if (remove) { deleteTransaction(remove.dataset.id); }
    });
    $('#transaction-form').addEventListener('submit', async event => {
      event.preventDefault();
      if (!editingTransactionId) return;
      const form = event.currentTarget;
      const data = new FormData(form);
      const body = {
        date: data.get('date'),
        amount: Number(data.get('amount')),
        direction: data.get('direction'),
        merchant: data.get('merchant'),
        note: data.get('note'),
        category: data.get('category'),
      };
      try {
        const result = await request(`/transactions/${encodeURIComponent(editingTransactionId)}`, {
          method: 'PUT', body,
        });
        const index = state.transactions.findIndex(item => item.id === editingTransactionId);
        if (index >= 0) state.transactions[index] = result.transaction;
        renderTransactions();
        $('#transaction-dialog').close();
        toast('流水已更新');
      } catch (error) {
        toast(error.message, true);
      }
    });
    $$('#transaction-dialog [data-dialog-close]').forEach(button =>
      button.addEventListener('click', () => $('#transaction-dialog').close()));
    $('#bill-file').addEventListener('change', event => uploadFile(event.target.files[0]));
    const dropzone = $('#dropzone');
    ['dragenter', 'dragover'].forEach(name => dropzone.addEventListener(name, event => {
      event.preventDefault();
      dropzone.classList.add('dragging');
    }));
    ['dragleave', 'drop'].forEach(name => dropzone.addEventListener(name, event => {
      event.preventDefault();
      dropzone.classList.remove('dragging');
    }));
    dropzone.addEventListener('drop', event => uploadFile(event.dataTransfer.files[0]));
    $('#theme-select').addEventListener('change', async event => {
      applyTheme(event.target.value);
      try { await saveSettings({ theme: event.target.value }); }
      catch (error) { toast(error.message, true); }
    });
    $('#profile-open').addEventListener('click', () => $('#profile-dialog').showModal());
    $$('[data-dialog-close]').forEach(button => button.addEventListener('click', () => $('#profile-dialog').close()));
    $('#profile-form').addEventListener('submit', async event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      try {
        const result = await request('/profile', {
          method: 'PUT',
          body: { displayName: form.get('displayName'), avatar: form.get('avatar') },
        });
        state.profile = result.profile;
        renderProfile();
        $('#profile-dialog').close();
        toast('个性化设置已保存');
      } catch (error) { toast(error.message, true); }
    });
    $('#large-threshold').addEventListener('change', async event => {
      try { await saveSettings({ largeThreshold: event.target.value }); toast('门槛已更新'); }
      catch (error) { toast(error.message, true); renderRules(); }
    });
    $('#rule-form').addEventListener('submit', async event => {
      event.preventDefault();
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      const rules = [...state.settings.rules, {
        id: (crypto.randomUUID && crypto.randomUUID()) || `${Date.now()}`,
        keyword: form.get('keyword'),
        amount: form.get('amount') ? Number(form.get('amount')) : null,
        category: form.get('category'),
        bucket: form.get('bucket'),
      }];
      try {
        await saveSettings({ rules });
        formElement.reset();
        toast('规则已保存');
      } catch (error) { toast(error.message, true); }
    });
    $('#rules-list').addEventListener('click', async event => {
      const button = event.target.closest('.delete-rule');
      if (!button) return;
      try {
        await saveSettings({ rules: state.settings.rules.filter(rule => rule.id !== button.dataset.id) });
        toast('规则已删除');
      } catch (error) { toast(error.message, true); }
    });
    $('#asset-form').addEventListener('submit', async event => {
      event.preventDefault();
      const formElement = event.currentTarget;
      const form = new FormData(formElement);
      const item = Object.fromEntries(form.entries());
      for (const key of ['quantity', 'costPrice', 'currentPrice']) item[key] = Number(item[key]);
      try {
        const result = await request('/items', { method: 'POST', body: item });
        state.items.unshift(result.item);
        renderAssets();
        formElement.reset();
        formElement.elements.quantity.value = '1';
        formElement.elements.costPrice.value = '0';
        formElement.elements.currentPrice.value = '0';
        toast('资产已保存');
      } catch (error) { toast(error.message, true); }
    });
    $('#assets-list').addEventListener('click', async event => {
      const button = event.target.closest('.delete-asset');
      if (!button || !window.confirm('确定删除这项资产记录吗？')) return;
      try {
        await request(`/items/${encodeURIComponent(button.dataset.id)}`, { method: 'DELETE' });
        state.items = state.items.filter(item => item.id !== button.dataset.id);
        renderAssets();
        toast('资产记录已删除');
      } catch (error) { toast(error.message, true); }
    });
    $('#new-chat').addEventListener('click', newConversation);
    $('#delete-conversation').addEventListener('click', deleteCurrentConversation);
    $('#conversation-list').addEventListener('click', event => {
      const button = event.target.closest('.conversation-item');
      if (button && button.dataset.id !== state.activeConversationId) {
        openConversation(button.dataset.id);
      }
    });
    $('#chat-messages').addEventListener('click', event => {
      const suggestion = event.target.closest('.suggestion');
      if (!suggestion) return;
      $('#chat-input').value = suggestion.dataset.question || '';
      $('#chat-input').focus();
    });
    $('#chat-composer').addEventListener('submit', event => {
      event.preventDefault();
      sendMessage();
    });
    $('#chat-input').addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
      }
    });
    $('#llm-form').addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const body = {
        enabled: data.get('enabled') === 'on',
        baseUrl: data.get('baseUrl'),
        model: data.get('model'),
        timeout: Number(data.get('timeout')),
        clearApiKey: data.get('clearApiKey') === 'on',
      };
      const apiKey = String(data.get('apiKey') || '').trim();
      if (apiKey) body.apiKey = apiKey;
      try {
        state.llmConfig = await request('/llm/config', { method: 'PUT', body });
        renderLlmForm();
        renderQaMode();
        toast('大模型配置已保存');
      } catch (error) {
        toast(error.message, true);
        renderLlmForm();
      }
    });
    $('#monthly-budget').addEventListener('change', async event => {
      const raw = event.target.value;
      const value = raw === '' ? null : Number(raw);
      try {
        await saveSettings({ monthlyBudget: value });
        await reloadReminders();
        toast('每月预算已更新');
      } catch (error) {
        toast(error.message, true);
        event.target.value = state.settings.monthlyBudget || '';
      }
    });
    document.addEventListener('change', async event => {
      const select = event.target.closest('.category-select');
      if (!select || !select.value) return;
      let category = select.value;
      if (category === '__custom__') {
        category = window.prompt('输入新的分类名称：', '');
        if (!category || !category.trim()) { renderTransactions(); return; }
        category = category.trim();
      }
      try { await updateCategory(select.dataset.id, category); }
      catch (error) { toast(error.message, true); renderTransactions(); }
    });
  }

  async function start() {
    const today = new Date();
    $('#today-label').textContent = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'full' }).format(today);
    $('#month-select').value = currentMonth;
    $('#month-select-secondary').value = currentMonth;
    bindEvents();
    try {
      const [profile, settings, transactions, items, panels, llmConfig, conversations] = await Promise.all([
        request('/profile'), request('/settings'), request('/transactions'),
        request('/items'), request('/panels'), request('/llm/config'), request('/conversations'),
      ]);
      state.profile = profile.profile;
      state.settings = settings;
      state.transactions = transactions.transactions;
      state.items = items.items;
      state.panels = panels;
      state.llmConfig = llmConfig;
      state.conversations = conversations.conversations;
      applyTheme(settings.theme || 'light');
      $('#monthly-budget').value = settings.monthlyBudget || '';
      reloadReminders();
      render();
      await initAssistant();
    } catch (error) {
      toast(`加载本地数据失败：${error.message}`, true);
      $('#recent-transactions').innerHTML = `<p class="empty-note">${escapeHtml(error.message)}</p>`;
      $('#all-transactions').innerHTML = `<p class="empty-note">${escapeHtml(error.message)}</p>`;
    }
  }

  start();
})();
