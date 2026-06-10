const tg = window.Telegram?.WebApp;
const state = {
  controllers: [],
  filter: "all",
  query: "",
  loading: false,
  view: "status",
  history: [],
  isAdmin: false,
  users: [],
  preferences: {
    theme: "dark",
    offline_delay_seconds: 0,
  },
};

const elements = {
  refreshButton: document.querySelector("#refreshButton"),
  totalCount: document.querySelector("#totalCount"),
  onlineCount: document.querySelector("#onlineCount"),
  offlineCount: document.querySelector("#offlineCount"),
  searchInput: document.querySelector("#searchInput"),
  statusLine: document.querySelector("#statusLine"),
  controllerList: document.querySelector("#controllerList"),
  historyList: document.querySelector("#historyList"),
  template: document.querySelector("#controllerTemplate"),
  historyTemplate: document.querySelector("#historyTemplate"),
  segments: Array.from(document.querySelectorAll(".segment")),
  viewTabs: Array.from(document.querySelectorAll(".view-tab")),
  panels: Array.from(document.querySelectorAll("[data-panel]")),
  themeButtons: Array.from(document.querySelectorAll("[data-theme]")),
  delaySelect: document.querySelector("#delaySelect"),
  adminPanel: document.querySelector("#adminPanel"),
  userInput: document.querySelector("#userInput"),
  addUserButton: document.querySelector("#addUserButton"),
  removeUserButton: document.querySelector("#removeUserButton"),
  userList: document.querySelector("#userList"),
};

tg?.ready();
tg?.expand();

elements.refreshButton.addEventListener("click", () => loadControllers());
elements.searchInput.addEventListener("input", (event) => {
  state.query = event.target.value.trim().toLowerCase();
  renderControllers();
});

for (const button of elements.segments) {
  if (!button.dataset.filter) {
    continue;
  }
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    for (const item of elements.segments) {
      item.classList.toggle("is-active", item === button);
    }
    renderControllers();
  });
}

for (const button of elements.themeButtons) {
  button.addEventListener("click", () => {
    savePreferences({ theme: button.dataset.theme });
  });
}

elements.delaySelect.addEventListener("change", () => {
  savePreferences({ offline_delay_seconds: Number(elements.delaySelect.value) });
});
elements.addUserButton.addEventListener("click", () => changeUser("POST"));
elements.removeUserButton.addEventListener("click", () => changeUser("DELETE"));

for (const tab of elements.viewTabs) {
  tab.addEventListener("click", () => setView(tab.dataset.view));
}

boot();

async function boot() {
  await loadMe();
  await loadPreferences();
  await loadControllers();
  await loadHistory();
  if (state.isAdmin) {
    await loadUsers();
  }
}

async function loadMe() {
  if (!tg?.initData) {
    return;
  }
  const response = await fetch("/api/me", { headers: authHeaders() });
  const payload = await response.json();
  if (response.ok) {
    state.isAdmin = Boolean(payload.is_admin);
    elements.adminPanel.hidden = !state.isAdmin;
    for (const button of elements.themeButtons) {
      if (button.dataset.theme === "matrix") {
        button.hidden = !state.isAdmin;
      }
    }
  }
}

async function loadPreferences() {
  if (!tg?.initData) {
    applyPreferences(state.preferences);
    return;
  }
  try {
    const response = await fetch("/api/preferences", {
      headers: authHeaders(),
    });
    const payload = await response.json();
    if (response.ok && payload.preferences) {
      state.preferences = { ...state.preferences, ...payload.preferences };
    }
  } finally {
    applyPreferences(state.preferences);
  }
}

async function savePreferences(values) {
  state.preferences = { ...state.preferences, ...values };
  applyPreferences(state.preferences);
  try {
    const response = await fetch("/api/preferences", {
      method: "POST",
      headers: {
        ...authHeaders(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(values),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.message || payload.error || "Ошибка сохранения");
    }
    state.preferences = { ...state.preferences, ...payload.preferences };
    applyPreferences(state.preferences);
  } catch (error) {
    elements.statusLine.textContent = `Настройки не сохранены: ${error.message}`;
  }
}

function applyPreferences(preferences) {
  document.documentElement.dataset.theme = preferences.theme || "dark";
  for (const button of elements.themeButtons) {
    button.classList.toggle("is-active", button.dataset.theme === preferences.theme);
  }
  elements.delaySelect.value = String(preferences.offline_delay_seconds ?? 0);
}

function setView(view) {
  state.view = view;
  for (const tab of elements.viewTabs) {
    tab.classList.toggle("is-active", tab.dataset.view === view);
  }
  for (const panel of elements.panels) {
    panel.hidden = panel.dataset.panel !== view;
  }
  if (view === "history") {
    loadHistory();
  }
  if (view === "settings" && state.isAdmin) {
    loadUsers();
  }
}

async function loadControllers() {
  if (state.loading) {
    return;
  }
  if (!tg?.initData) {
    renderError("Откройте приложение из Telegram, чтобы подтвердить доступ.");
    return;
  }

  state.loading = true;
  elements.refreshButton.disabled = true;
  elements.statusLine.textContent = "Обновляю статус...";

  try {
    const response = await fetch("/api/controllers", {
      headers: authHeaders(),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.message || payload.error || "Ошибка запроса");
    }
    state.controllers = payload.controllers || [];
    renderSummary(payload.summary || {});
    renderControllers();
  } catch (error) {
    renderError(`Не удалось получить данные: ${error.message}`);
  } finally {
    state.loading = false;
    elements.refreshButton.disabled = false;
  }
}

async function loadHistory() {
  if (!tg?.initData) {
    return;
  }
  try {
    const response = await fetch("/api/history", { headers: authHeaders() });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.message || payload.error || "Ошибка истории");
    }
    state.history = payload.events || [];
    renderHistory();
  } catch (error) {
    renderHistoryError(error.message);
  }
}

async function loadUsers() {
  if (!state.isAdmin) {
    return;
  }
  const response = await fetch("/api/users", { headers: authHeaders() });
  const payload = await response.json();
  if (response.ok) {
    state.users = payload.users || [];
    renderUsers();
  }
}

async function changeUser(method) {
  const value = elements.userInput.value.trim();
  if (!value) {
    return;
  }
  const response = await fetch("/api/users", {
    method,
    headers: {
      ...authHeaders(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ user: value }),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    elements.statusLine.textContent = payload.message || payload.error || "Не удалось изменить пользователя";
    return;
  }
  elements.userInput.value = "";
  await loadUsers();
}

function renderSummary(summary) {
  elements.totalCount.textContent = summary.total ?? state.controllers.length;
  elements.onlineCount.textContent = summary.online ?? state.controllers.filter((item) => item.online).length;
  elements.offlineCount.textContent = summary.offline ?? state.controllers.filter((item) => !item.online).length;
}

function renderControllers() {
  const visible = state.controllers.filter((controller) => {
    if (state.filter === "online" && !controller.online) {
      return false;
    }
    if (state.filter === "offline" && controller.online) {
      return false;
    }
    if (!state.query) {
      return true;
    }
    return `${controller.name} ${controller.id}`.toLowerCase().includes(state.query);
  });

  elements.controllerList.replaceChildren();
  elements.statusLine.textContent = `Показано: ${visible.length} из ${state.controllers.length}`;

  if (visible.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Нет объектов под выбранный фильтр.";
    elements.controllerList.append(empty);
    return;
  }

  for (const controller of visible) {
    const node = elements.template.content.firstElementChild.cloneNode(true);
    node.classList.toggle("is-offline", !controller.online);
    node.querySelector("h2").textContent = controller.name || controller.id;
    node.querySelector(".serial").textContent = controller.id;
    node.querySelector(".organization").textContent = controller.organization_id
      ? `Организация: ${shortId(controller.organization_id)}`
      : "";
    node.querySelector(".last-seen").textContent = controller.last_seen
      ? `Последний пинг: ${formatDate(controller.last_seen)}`
      : "Последний пинг: нет данных";
    const openLink = node.querySelector(".open-link");
    const hasLocalAccess = !controller.online && controller.local_url;
    openLink.href = controller.access_url || controller.remote_url;
    openLink.textContent = hasLocalAccess ? "Локально" : "Открыть";
    elements.controllerList.append(node);
  }
}

function renderHistory() {
  elements.historyList.replaceChildren();
  if (state.history.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "История пока пустая.";
    elements.historyList.append(empty);
    return;
  }
  for (const event of state.history) {
    const node = elements.historyTemplate.content.firstElementChild.cloneNode(true);
    node.classList.toggle("is-offline", event.type === "offline");
    node.querySelector(".history-status").textContent = event.type === "online" ? "UP" : "DOWN";
    node.querySelector("h2").textContent = event.name || event.id;
    node.querySelector("p").textContent = `${formatTimestamp(event.timestamp)}${event.organization_id ? ` · ${shortId(event.organization_id)}` : ""}`;
    elements.historyList.append(node);
  }
}

function renderHistoryError(message) {
  elements.historyList.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = `Не удалось загрузить историю: ${message}`;
  elements.historyList.append(empty);
}

function renderUsers() {
  elements.userList.replaceChildren();
  for (const user of state.users) {
    const row = document.createElement("div");
    row.className = "user-row";
    const username = user.username ? `@${user.username}` : "без username";
    row.textContent = `${username} · ${user.id}${user.admin ? " · админ" : ""}`;
    elements.userList.append(row);
  }
}

function renderError(message) {
  elements.statusLine.textContent = message;
  elements.controllerList.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = message;
  elements.controllerList.append(empty);
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatTimestamp(value) {
  return formatDate(new Date(value * 1000).toISOString());
}

function authHeaders() {
  return {
    Authorization: `tma ${tg.initData}`,
  };
}

function shortId(value) {
  return value.length > 12 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}
