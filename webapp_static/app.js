const tg = window.Telegram?.WebApp;
const state = {
  controllers: [],
  filter: "all",
  query: "",
  loading: false,
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
};

tg?.ready();
tg?.expand();

elements.refreshButton.addEventListener("click", () => loadControllers());
elements.searchInput.addEventListener("input", (event) => {
  state.query = event.target.value.trim().toLowerCase();
  renderControllers();
});

for (const button of elements.segments) {
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    for (const item of elements.segments) {
      item.classList.toggle("is-active", item === button);
    }
    renderControllers();
  });
}

loadControllers();

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
      headers: {
        Authorization: `tma ${tg.initData}`,
      },
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
    node.querySelector(".last-seen").textContent = controller.last_seen
      ? `Последний пинг: ${formatDate(controller.last_seen)}`
      : "Последний пинг: нет данных";
    node.querySelector(".open-link").href = controller.remote_url;
    elements.controllerList.append(node);
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
