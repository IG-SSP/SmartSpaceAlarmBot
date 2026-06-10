const tg = window.Telegram?.WebApp;
const state = {
  controllers: [],
  filter: "all",
  query: "",
  loading: false,
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
  template: document.querySelector("#controllerTemplate"),
  segments: Array.from(document.querySelectorAll(".segment")),
  themeButtons: Array.from(document.querySelectorAll("[data-theme]")),
  delaySelect: document.querySelector("#delaySelect"),
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

boot();

async function boot() {
  await loadPreferences();
  await loadControllers();
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
    const pingButton = node.querySelector(".ping-button");
    const hasLocalAccess = !controller.online && controller.local_url;
    openLink.href = controller.access_url || controller.remote_url;
    openLink.textContent = hasLocalAccess ? "Локально" : "Открыть";
    pingButton.hidden = !hasLocalAccess;
    pingButton.addEventListener("click", () => pingLocalAccess(controller));
    elements.controllerList.append(node);
  }
}

async function pingLocalAccess(controller) {
  if (!controller.local_url) {
    return;
  }
  const ok = window.confirm("Перед проверкой включите VPN до объекта. Проверить локальный веб-интерфейс сейчас?");
  if (!ok) {
    return;
  }

  const startedAt = performance.now();
  try {
    await probeImage(`${controller.local_url.replace(/\/$/, "")}/favicon.ico?_=${Date.now()}`, 3500);
    const elapsed = Math.max(1, Math.round(performance.now() - startedAt));
    elements.statusLine.textContent = `Локальный доступ ответил: ${controller.local_ip}, примерно ${elapsed} мс.`;
  } catch (error) {
    elements.statusLine.textContent = `Локальный доступ не ответил. Проверьте VPN до объекта и адрес ${controller.local_ip}.`;
  }
}

function probeImage(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const timer = window.setTimeout(() => {
      image.onload = null;
      image.onerror = null;
      reject(new Error("timeout"));
    }, timeoutMs);
    image.onload = () => {
      window.clearTimeout(timer);
      resolve();
    };
    image.onerror = () => {
      window.clearTimeout(timer);
      resolve();
    };
    image.src = url;
  });
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

function authHeaders() {
  return {
    Authorization: `tma ${tg.initData}`,
  };
}

function shortId(value) {
  return value.length > 12 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}
