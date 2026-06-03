document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-fill]");
  if (!button) return;
  const input = document.getElementById("taskInput");
  if (input) input.value = button.dataset.fill;
});

const searchForm = document.querySelector("[data-search-form]");
if (searchForm) {
  const statusUrl = searchForm.dataset.statusUrl;
  const stopUrl = searchForm.dataset.stopUrl;
  const projectId = searchForm.dataset.projectId || "";
  const submitButtons = Array.from(searchForm.querySelectorAll("button[type='submit']"));
  const textArea = searchForm.querySelector("textarea");
  const searchModeInput = searchForm.querySelector("[data-search-mode-input]");
  const stopButton = searchForm.querySelector("[data-stop-search-task]");

  if (projectId && textArea) {
    const prefillKey = `guangming-search-prefill:${projectId}`;
    try {
      const payload = JSON.parse(window.localStorage.getItem(prefillKey) || "null");
      if (payload?.request && !textArea.value.trim()) {
        textArea.value = payload.request;
        if (searchModeInput && payload.mode) searchModeInput.value = payload.mode;
      }
      window.localStorage.removeItem(prefillKey);
    } catch (_error) {
      window.localStorage.removeItem(prefillKey);
    }
  }

  submitButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (searchModeInput && button.dataset.searchMode) {
        searchModeInput.value = button.dataset.searchMode;
      }
    });
  });

  searchForm.addEventListener("submit", (event) => {
    const submitter = event.submitter;
    if (searchModeInput && submitter?.dataset.searchMode) {
      searchModeInput.value = submitter.dataset.searchMode;
    }
    submitButtons.forEach((button) => {
      button.disabled = true;
      if (button === submitter) {
        button.classList.add("is-loading");
        const icon = button.querySelector(".send-btn-icon");
        if (icon) icon.textContent = "...";
      }
    });
    if (textArea) textArea.readOnly = true;
  });

  const pollStatus = async () => {
    if (!statusUrl) return;
    try {
      const response = await fetch(statusUrl, { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      const eventList = document.querySelector("[data-task-events]");
      if (eventList && payload.latest?.events) {
        eventList.innerHTML = payload.latest.events
          .slice(-6)
          .map((event) => `<div class="task-event-item">${escapeHtml(event.message || "")}</div>`)
          .join("");
      }
      if (!payload.running && document.querySelector(".chat-message.running")) {
        window.location.reload();
      }
    } catch (_error) {
      // Let the next polling cycle retry.
    }
  };

  if (document.querySelector(".chat-message.running")) {
    window.setInterval(pollStatus, 5000);
  }

  stopButton?.addEventListener("click", async () => {
    if (!stopUrl) return;
    stopButton.disabled = true;
    try {
      await fetch(stopUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
      });
    } finally {
      window.location.reload();
    }
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>\"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  })[char]);
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#96;");
}

function renderMarkdown(value) {
  const source = String(value || "");
  if (!window.marked || !window.DOMPurify) {
    return escapeHtml(source);
  }
  window.marked.setOptions({
    breaks: true,
    gfm: true,
  });
  const rawHtml = window.marked.parse(source);
  return window.DOMPurify.sanitize(rawHtml);
}

function hydrateMarkdownBlocks(root = document) {
  root.querySelectorAll("[data-markdown]").forEach((block) => {
    if (block.dataset.markdownRendered === "true") return;
    block.innerHTML = renderMarkdown(block.textContent || "");
    block.dataset.markdownRendered = "true";
    block.querySelectorAll("a").forEach((link) => {
      link.target = "_blank";
      link.rel = "noreferrer";
    });
  });
}

hydrateMarkdownBlocks();

document.addEventListener("submit", async (event) => {
  const importForm = event.target.closest("[data-import-form]");
  const removeForm = event.target.closest("[data-remove-form]");
  if (!importForm && !removeForm) return;

  event.preventDefault();
  const form = importForm || removeForm;
  const mode = importForm ? "import" : "remove";
  const button = form.querySelector("button[type='submit']");
  const card = form.closest(".result-card");
  const status = card?.querySelector("[data-import-status]");
  const originalText = button?.textContent || "";

  if (button) {
    button.disabled = true;
    button.textContent = mode === "import" ? "导入中..." : "移除中...";
  }

  try {
    const response = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: {
        Accept: "application/json",
        "X-Requested-With": "fetch",
      },
    });
    if (!response.ok) throw new Error("library action failed");

    const payload = await response.json();
    updateProjectPaperCount(form, payload);

    if (mode === "import") {
      if (status) status.textContent = payload.status || "已导入";
      switchLibraryAction(form, "remove");
    } else {
      if (status) status.textContent = payload.status || "未导入";
      switchLibraryAction(form, "import");
    }
  } catch (_error) {
    if (button) {
      button.disabled = false;
      button.textContent = originalText || (mode === "import" ? "导入知识库" : "从知识库移除");
    }
    if (status) status.textContent = mode === "import" ? "导入失败" : "移除失败";
  }
});

function updateProjectPaperCount(form, payload) {
  const currentProjectId = form.action.match(/\/projects\/([^/]+)\//)?.[1];
  const countTarget = currentProjectId
    ? document.querySelector(`[data-project-paper-count="${currentProjectId}"]`)
    : null;
  if (countTarget && payload.stats?.papers !== undefined) {
    countTarget.textContent = `${payload.stats.papers} 篇文献`;
  }
}

function switchLibraryAction(form, nextMode) {
  const button = form.querySelector("button[type='submit']");
  const hidden = form.querySelector("input[name='paper_id'], input[name='candidate_id']");
  if (nextMode === "remove") {
    form.removeAttribute("data-import-form");
    form.setAttribute("data-remove-form", "");
    form.action = form.action.replace("/papers/import", "/papers/remove");
    if (hidden) hidden.name = "candidate_id";
    if (button) {
      button.disabled = false;
      button.textContent = "从知识库移除";
      button.classList.add("danger");
    }
    return;
  }

  form.removeAttribute("data-remove-form");
  form.setAttribute("data-import-form", "");
  form.action = form.action.replace("/papers/remove", "/papers/import");
  if (hidden) hidden.name = "paper_id";
  if (button) {
    button.disabled = false;
    button.textContent = "导入知识库";
    button.classList.remove("danger");
  }
}

document.querySelectorAll("[data-copy-text]").forEach((button) => {
  button.addEventListener("click", async () => {
    const text = button.dataset.copyText || "";
    if (!text) return;
    const original = button.textContent;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        fallbackCopyText(text);
      }
      button.textContent = "已复制";
      window.setTimeout(() => {
        button.textContent = original;
      }, 1200);
    } catch (_error) {
      try {
        fallbackCopyText(text);
        button.textContent = "已复制";
        window.setTimeout(() => {
          button.textContent = original;
        }, 1200);
      } catch (_fallbackError) {
        button.textContent = "复制失败";
        window.setTimeout(() => {
          button.textContent = original;
        }, 1200);
      }
    }
  });
});

function fallbackCopyText(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!ok) throw new Error("copy failed");
}

const selectAll = document.querySelector("[data-select-all]");
const paperChecks = Array.from(document.querySelectorAll("[data-paper-select]"));
const bulkButtons = Array.from(document.querySelectorAll("[data-bulk-action]"));
const selectionSummary = document.querySelector("[data-selection-summary]");
const chatSelectionHint = document.querySelector("[data-chat-selection-hint]");

function updateLibrarySelection() {
  const selectedCount = paperChecks.filter((item) => item.checked).length;
  if (selectionSummary) {
    selectionSummary.textContent = selectedCount ? `已选择 ${selectedCount} 篇文献` : "未选择文献";
  }
  if (chatSelectionHint) {
    chatSelectionHint.textContent = selectedCount ? `已关联 ${selectedCount} 篇文献` : "请先勾选文献";
  }
  bulkButtons.forEach((button) => {
    button.disabled = selectedCount === 0;
  });
  if (selectAll) {
    selectAll.checked = selectedCount > 0 && selectedCount === paperChecks.length;
    selectAll.indeterminate = selectedCount > 0 && selectedCount < paperChecks.length;
  }
}

if (selectAll && paperChecks.length) {
  selectAll.addEventListener("change", () => {
    paperChecks.forEach((item) => {
      item.checked = selectAll.checked;
    });
    updateLibrarySelection();
  });
}

paperChecks.forEach((item) => {
  item.addEventListener("change", updateLibrarySelection);
});

updateLibrarySelection();

const hiddenProgressKinds = new Set();

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-dismiss-progress]");
  if (!button) return;
  const box = button.closest("[data-progress-kind]");
  if (!box) return;
  hiddenProgressKinds.add(box.dataset.progressKind || "");
  box.classList.remove("is-active");
  box.innerHTML = "";
});

function updatePdfCells(papers) {
  (papers || []).forEach((paper) => {
    const cell = document.querySelector(`[data-pdf-cell="${CSS.escape(paper.paper_id || "")}"]`);
    if (!cell) return;
    const status = paper.pdf_status || "无来源";
    const pdfCell = cell.querySelector(".pdf-cell");
    if (!pdfCell) return;
    if (status === "已下载" || status === "手动导入") {
      const uploadForm = pdfCell.querySelector(".pdf-upload-form");
      const openUrl = cell.dataset.openPdfUrl || "#";
      pdfCell.innerHTML = `<a class="ghost-btn small" href="${escapeAttribute(openUrl)}" target="_blank" onclick="event.stopPropagation()" title="打开本地 PDF">打开</a>`;
      if (uploadForm) pdfCell.appendChild(uploadForm);
      return;
    }
    pdfCell.querySelector("span")?.remove();
    let statusNode = pdfCell.querySelector("[data-pdf-status-text]");
    if (!statusNode) {
      statusNode = document.createElement("span");
      statusNode.dataset.pdfStatusText = "";
      pdfCell.insertBefore(statusNode, pdfCell.firstChild);
    }
    statusNode.textContent = status;
  });
}

const importModal = document.querySelector("[data-import-modal]");
if (importModal) {
  const importForm = importModal.querySelector("[data-import-form]");
  const draftList = importModal.querySelector("[data-import-draft-list]");
  const statusBox = importModal.querySelector("[data-import-status-box]");
  const confirmButton = importModal.querySelector("[data-confirm-import]");
  const cancelButton = importModal.querySelector("[data-cancel-import]");
  const fileInput = importModal.querySelector("[data-import-file-input]");
  const fileButton = importModal.querySelector("[data-import-file-button]");
  const fileSummary = importModal.querySelector("[data-import-file-summary]");
  const summary = importModal.querySelector("[data-import-summary]");
  let importPollTimer = null;

  const openImportModal = () => importModal.classList.add("is-open");
  const closeImportModal = () => importModal.classList.remove("is-open");

  const renderImportStatus = (task) => {
    if (!statusBox) return;
    if (!task) {
      statusBox.classList.remove("is-active");
      statusBox.innerHTML = "";
      return;
    }
    statusBox.classList.add("is-active");
    const total = Number(task.total || 0);
    const completed = Number(task.completed || 0);
    const failed = Number(task.failed || 0);
    const percent = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
    const events = Array.isArray(task.events)
      ? task.events.slice(-6).map((event) => `<div class="task-event-item">${escapeHtml(event.message || "")}</div>`).join("")
      : "";
    statusBox.innerHTML = `
      <div class="matrix-progress-head">
        <strong>${task.status === "running" ? "正在补全文献信息" : "导入解析任务已结束"}</strong>
        <span>${completed} / ${total}${failed ? `，失败 ${failed}` : ""}</span>
      </div>
      <div class="matrix-progress-bar"><span style="width:${percent}%"></span></div>
      <div class="task-event-list" data-import-events>${events}</div>
    `;
  };

  const renderDrafts = (drafts) => {
    if (!draftList) return;
    const items = drafts || [];
    draftList.innerHTML = items.map((draft) => {
      const metadata = draft.metadata || {};
      const title = metadata.title || draft.raw_input || draft.filename || "未命名导入项";
      const detail = [
        metadata.year || "未知年份",
        metadata.venue || "未知来源",
        metadata.doi || "无 DOI",
      ].join(" · ");
      const status = draft.status || "pending";
      const selectable = status === "ready";
      const duplicateText = draft.duplicate_action === "attach_pdf" ? " · 将补充 PDF 到已有文献" : "";
      return `
        <article class="import-draft-card ${escapeAttribute(status)}" data-draft-id="${escapeAttribute(draft.draft_id || "")}">
          <label class="check-wrap">
            <input type="checkbox" data-import-draft-check value="${escapeAttribute(draft.draft_id || "")}" ${selectable ? "" : "disabled"}>
          </label>
          <div>
            <strong>${escapeHtml(title)}</strong>
            <span>${escapeHtml(status)}${draft.error ? ` · ${escapeHtml(draft.error)}` : ""}${duplicateText}</span>
            ${metadata.title ? `<small>${escapeHtml(detail)}</small>` : ""}
          </div>
        </article>
      `;
    }).join("");
    if (summary) {
      const readyCount = items.filter((item) => item.status === "ready").length;
      summary.textContent = readyCount ? `已有 ${readyCount} 条可导入暂存项。` : "等待解析成功后选择暂存项导入知识库。";
    }
  };

  const pollImportStatus = async () => {
    const response = await fetch(importModal.dataset.statusUrl, { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    renderImportStatus(payload.latest);
    renderDrafts(payload.drafts || []);
    if (!payload.running) {
      window.clearInterval(importPollTimer);
      importPollTimer = null;
    }
  };

  const startImportPolling = () => {
    if (importPollTimer) return;
    importPollTimer = window.setInterval(pollImportStatus, 2500);
  };

  document.querySelector("[data-open-import-modal]")?.addEventListener("click", openImportModal);
  importModal.querySelector("[data-close-import-modal]")?.addEventListener("click", closeImportModal);
  fileButton?.addEventListener("click", () => fileInput?.click());
  fileInput?.addEventListener("change", () => {
    const files = Array.from(fileInput.files || []);
    if (!fileSummary) return;
    fileSummary.textContent = files.length ? `已选择 ${files.length} 个 PDF 文件` : "尚未选择文件";
  });

  importForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = importForm.querySelector("[data-start-import]");
    button.disabled = true;
    try {
      const response = await fetch(importModal.dataset.runUrl, {
        method: "POST",
        body: new FormData(importForm),
        headers: {
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "import run failed");
      renderImportStatus(payload.task);
      renderDrafts(payload.drafts || []);
      startImportPolling();
    } catch (error) {
      window.alert(error.message || "启动导入解析失败。");
    } finally {
      button.disabled = false;
    }
  });

  confirmButton?.addEventListener("click", async () => {
    const draftIds = Array.from(importModal.querySelectorAll("[data-import-draft-check]:checked")).map((item) => item.value);
    if (!draftIds.length) {
      window.alert("请先勾选解析成功的暂存项。");
      return;
    }
    confirmButton.disabled = true;
    try {
      const response = await fetch(importModal.dataset.confirmUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
        body: JSON.stringify({ draft_ids: draftIds }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "confirm import failed");
      window.location.reload();
    } catch (error) {
      window.alert(error.message || "确认导入失败。");
      confirmButton.disabled = false;
    }
  });

  cancelButton?.addEventListener("click", async () => {
    cancelButton.disabled = true;
    try {
      const response = await fetch(importModal.dataset.cancelUrl, {
        method: "POST",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "cancel import failed");
      renderImportStatus(null);
      renderDrafts([]);
      importForm?.reset();
      if (fileSummary) fileSummary.textContent = "尚未选择文件";
      closeImportModal();
    } catch (error) {
      window.alert(error.message || "取消导入失败。");
    } finally {
      cancelButton.disabled = false;
    }
  });

  if (importModal.classList.contains("is-open")) {
    startImportPolling();
  }
}

const libraryChatForm = document.querySelector("[data-library-chat-form]");
if (libraryChatForm) {
  const selectedInputs = libraryChatForm.querySelector("[data-library-selected-inputs]");
  const textarea = libraryChatForm.querySelector("textarea");
  const submitButton = libraryChatForm.querySelector(".library-chat-controls .send-btn");
  const statusUrl = libraryChatForm.dataset.statusUrl;
  const resetUrl = libraryChatForm.dataset.resetUrl;
  const stopUrl = libraryChatForm.dataset.stopUrl;
  const compactUrl = libraryChatForm.dataset.compactUrl;
  const projectId = libraryChatForm.dataset.projectId || "default";
  const compactButton = document.querySelector("[data-compact-library-chat]");
  const selectionStoreKey = `guangming-library-selection:${projectId}`;
  const chatWindow = document.querySelector("[data-library-chat-window]");
  const libraryQaPanel = document.querySelector(".library-qa-panel");
  let libraryChatPollTimer = null;

  const saveCurrentLibrarySelection = () => {
    const paperIds = paperChecks.filter((item) => item.checked).map((item) => item.value);
    window.localStorage.setItem(selectionStoreKey, JSON.stringify(paperIds));
  };

  const restoreLibrarySelection = () => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(selectionStoreKey) || "[]");
      if (!Array.isArray(saved)) return;
      const savedSet = new Set(saved);
      paperChecks.forEach((item) => {
        item.checked = savedSet.has(item.value);
      });
      updateLibrarySelection();
    } catch (_error) {
      // Ignore invalid localStorage content.
    }
  };

  const scrollChatToBottom = () => {
    if (chatWindow) chatWindow.scrollTop = chatWindow.scrollHeight;
  };

  const renderLibraryMessage = (message) => {
    if (message.role === "divider") {
      return `
        <div class="chat-divider">
          <span>${escapeHtml(message.content || "新的对话")}</span>
        </div>
      `;
    }
    const isUser = message.role === "user";
    const related = Array.isArray(message.selected_paper_ids) && message.selected_paper_ids.length
      ? `<div class="chat-result-line">本轮关联 ${message.selected_paper_ids.length} 篇勾选文献</div>`
      : "";
    const content = isUser
      ? `<p>${escapeHtml(message.content || "")}</p>`
      : `<div class="markdown-body">${renderMarkdown(message.content || "")}</div>`;
    return `
      <article class="chat-message ${isUser ? "user" : "assistant"}">
        <div class="chat-avatar">${isUser ? "我" : "光"}</div>
        <div class="chat-bubble">
          <div class="chat-meta">
            <span>${isUser ? "我的问题" : "知识库问答"}</span>
            <time>${escapeHtml(message.created_at || "")}</time>
          </div>
          ${content}
          ${related}
        </div>
      </article>
    `;
  };

  const renderRunningMessage = (task) => {
    if (!task) return "";
    const events = Array.isArray(task.events)
      ? task.events.slice(-6).map((event) => `<div class="task-event-item">${escapeHtml(event.message || "")}</div>`).join("")
      : "";
    const selectedCount = Array.isArray(task.selected_paper_ids) ? task.selected_paper_ids.length : 0;
    return `
      <article class="chat-message assistant running">
        <div class="chat-avatar">光</div>
        <div class="chat-bubble">
          <div class="chat-meta">
            <span>知识库问答</span>
            <time>${escapeHtml(task.started_at || "")}</time>
          </div>
          <p>正在围绕 ${selectedCount} 篇勾选文献回答问题。</p>
          <div class="chat-result-line loading-line">
            <span class="loading-dot"></span>
            问答中，请稍候
          </div>
          <div class="task-event-list" data-library-task-events>${events}</div>
        </div>
      </article>
    `;
  };

  const renderLibraryChat = (messages, task = null) => {
    if (!libraryQaPanel) return;
    let targetChatWindow = libraryQaPanel.querySelector("[data-library-chat-window]");
    if ((messages?.length || task) && !targetChatWindow) {
      libraryQaPanel.insertAdjacentHTML("afterbegin", '<div class="chat-window" data-library-chat-window></div>');
      targetChatWindow = libraryQaPanel.querySelector("[data-library-chat-window]");
    }
    if (!targetChatWindow) return;
    libraryQaPanel.classList.toggle("has-chat", Boolean(messages?.length || task));
    libraryQaPanel.classList.toggle("is-empty", !messages?.length && !task);
    if ((!messages || !messages.length) && !task) {
      targetChatWindow.remove();
      return;
    }
    targetChatWindow.innerHTML = [
      ...(messages || []).map(renderLibraryMessage),
      task && task.status === "running" ? renderRunningMessage(task) : "",
    ].join("");
    targetChatWindow.querySelectorAll(".markdown-body a").forEach((link) => {
      link.target = "_blank";
      link.rel = "noreferrer";
    });
    scrollChatToBottom();
  };

  const setLibraryChatBusy = (busy) => {
    if (submitButton) {
      submitButton.disabled = false;
      if (busy) {
        submitButton.type = "button";
        submitButton.classList.add("stop-send-btn");
        submitButton.setAttribute("data-stop-library-chat", "");
        submitButton.setAttribute("aria-label", "停止本次问答");
        submitButton.setAttribute("title", "停止本次问答");
        submitButton.innerHTML = '<span class="stop-icon"></span>';
      } else {
        submitButton.type = "submit";
        submitButton.classList.remove("stop-send-btn");
        submitButton.removeAttribute("data-stop-library-chat");
        submitButton.setAttribute("aria-label", "发送知识库问题");
        submitButton.setAttribute("title", "发送知识库问题");
        submitButton.textContent = "➤";
      }
    }
    if (textarea) textarea.readOnly = busy;
  };

  const startLibraryChatPolling = () => {
    if (libraryChatPollTimer) return;
    libraryChatPollTimer = window.setInterval(pollLibraryChatStatus, 3000);
  };

  const stopLibraryChatPolling = () => {
    if (!libraryChatPollTimer) return;
    window.clearInterval(libraryChatPollTimer);
    libraryChatPollTimer = null;
  };

  paperChecks.forEach((item) => {
    item.addEventListener("change", saveCurrentLibrarySelection);
  });
  if (selectAll) {
    selectAll.addEventListener("change", saveCurrentLibrarySelection);
  }
  restoreLibrarySelection();
  scrollChatToBottom();

  libraryChatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const paperIds = paperChecks.filter((item) => item.checked).map((item) => item.value);
    if (!paperIds.length) {
      window.alert("请先勾选要问答的文献。");
      return;
    }
    if (!textarea?.value.trim()) {
      window.alert("请输入知识库问题。");
      return;
    }
    selectedInputs.innerHTML = paperIds
      .map((paperId) => `<input type="hidden" name="paper_ids" value="${escapeAttribute(paperId)}">`)
      .join("");
    saveCurrentLibrarySelection();
    setLibraryChatBusy(true);
    try {
      const response = await fetch(libraryChatForm.action, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
        body: JSON.stringify({
          user_question: textarea.value.trim(),
          paper_ids: paperIds,
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "submit failed");
      if (textarea) textarea.value = "";
      renderLibraryChat(payload.messages || [payload.user_message].filter(Boolean), {
        run_id: payload.run_id,
        status: "running",
        selected_paper_ids: paperIds,
        started_at: "",
        events: [{ message: "知识库问答任务已提交。" }],
      });
      startLibraryChatPolling();
    } catch (error) {
      window.alert(error.message || "知识库问答提交失败。");
      setLibraryChatBusy(false);
    }
  });

  async function pollLibraryChatStatus() {
    if (!statusUrl) return;
    try {
      const response = await fetch(statusUrl, { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      renderLibraryChat(payload.messages || [], payload.running ? payload.latest : null);
      if (!payload.running) {
        stopLibraryChatPolling();
        setLibraryChatBusy(false);
      }
    } catch (_error) {
      // Let the next polling cycle retry.
    }
  }

  if (document.querySelector(".library-qa-panel .chat-message.running")) {
    setLibraryChatBusy(true);
    startLibraryChatPolling();
  }

  libraryChatForm.addEventListener("click", async (event) => {
    const stopButton = event.target.closest("[data-stop-library-chat]");
    if (!stopButton || !stopUrl) return;
    event.preventDefault();
    stopButton.disabled = true;
    try {
      const response = await fetch(stopUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "stop failed");
      stopLibraryChatPolling();
      setLibraryChatBusy(false);
      renderLibraryChat(payload.messages || []);
    } catch (error) {
      window.alert(error.message || "停止问答失败。");
      stopButton.disabled = false;
    }
  });

  document.querySelector("[data-reset-library-chat]")?.addEventListener("click", async () => {
    if (!resetUrl) return;
    if (!window.confirm("确定要重置知识库问答吗？这会开启新的对话线程，但保留上方历史记录分割线。")) return;
    try {
      const response = await fetch(resetUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "reset failed");
      stopLibraryChatPolling();
      setLibraryChatBusy(false);
      renderLibraryChat(payload.messages || []);
    } catch (error) {
      window.alert(error.message || "重置对话失败。");
    }
  });

  compactButton?.addEventListener("click", async () => {
    if (!compactUrl) return;
    if (!window.confirm("确定要压缩当前知识库问答记忆吗？这会保留当前线程，但让 Codex 尝试整理上下文。")) return;
    const previousText = compactButton.textContent;
    compactButton.disabled = true;
    compactButton.textContent = "压缩中...";
    try {
      const response = await fetch(compactUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "compact failed");
      stopLibraryChatPolling();
      setLibraryChatBusy(false);
      renderLibraryChat(payload.messages || []);
    } catch (error) {
      window.alert(error.message || "压缩记忆失败。");
    } finally {
      compactButton.disabled = false;
      compactButton.textContent = previousText || "压缩记忆";
    }
  });
}

const readingMatrixPanel = document.querySelector("[data-reading-matrix-panel]");
if (readingMatrixPanel) {
  const libraryQaPanelForMatrix = document.querySelector(".library-qa-panel");
  if (libraryQaPanelForMatrix && (readingMatrixPanel.compareDocumentPosition(libraryQaPanelForMatrix) & Node.DOCUMENT_POSITION_PRECEDING)) {
    libraryQaPanelForMatrix.before(readingMatrixPanel);
  }
  const fieldList = readingMatrixPanel.querySelector("[data-matrix-field-list]");
  const statusBox = readingMatrixPanel.querySelector("[data-reading-matrix-status]");
  const runButton = readingMatrixPanel.querySelector(".matrix-actions .send-btn");
  const stopButton = readingMatrixPanel.querySelector("[data-stop-reading-matrix]");
  const addButton = readingMatrixPanel.querySelector("[data-add-matrix-field]");
  const recommendButton = readingMatrixPanel.querySelector("[data-recommend-matrix-fields]");
  const saveButton = readingMatrixPanel.querySelector("[data-save-matrix-fields]");
  const matrixProjectId = readingMatrixPanel.dataset.projectId || "";
  let matrixPollTimer = null;
  let matrixFieldsDirty = false;

  const matrixRecommendSourceCount = () => {
    const selectedCount = paperChecks.filter((item) => item.checked).length;
    return selectedCount || paperChecks.length;
  };

  const updateMatrixRecommendLabel = () => {
    if (!recommendButton) return;
    recommendButton.textContent = `AI 推荐字段（关联 ${matrixRecommendSourceCount()} 篇）`;
  };

  const currentMatrixFields = () => Array.from(fieldList?.querySelectorAll("[data-field-id]") || [])
    .map((card, index) => ({
      field_id: card.dataset.fieldId,
      name: card.querySelector("[data-matrix-field-name]")?.value.trim() || "",
      rule: card.querySelector("[data-matrix-field-rule]")?.value.trim() || "",
      order: index + 1,
      enabled: true,
    }));

  const matrixFieldsReadyToSave = () => {
    const fields = currentMatrixFields();
    return fields.length > 0 && fields.every((field) => field.name);
  };

  const markMatrixDraftState = () => {
    fieldList?.querySelectorAll("[data-field-id]").forEach((card) => {
      const nameInput = card.querySelector("[data-matrix-field-name]");
      card.classList.toggle("is-draft-invalid", !nameInput?.value.trim());
    });
  };

  const setMatrixFieldsDirty = (dirty) => {
    matrixFieldsDirty = dirty;
    if (saveButton) {
      saveButton.textContent = dirty ? "保存字段*" : "保存字段";
      saveButton.classList.toggle("primary", dirty);
    }
  };

  const saveMatrixFields = async () => {
    const fields = currentMatrixFields();
    markMatrixDraftState();
    if (!readingMatrixPanel.dataset.fieldsUrl) return false;
    if (!matrixFieldsReadyToSave()) {
      window.alert("请先填写所有文献矩阵字段名称，再保存。");
      return false;
    }
    const response = await fetch(readingMatrixPanel.dataset.fieldsUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-Requested-With": "fetch",
      },
      body: JSON.stringify({ fields }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "fields save failed");
    setMatrixFieldsDirty(false);
    return true;
  };

  const renderMatrixFields = (fields) => {
    if (!fieldList) return;
    fieldList.innerHTML = (fields || []).map((field) => `
      <article class="matrix-field-card" data-field-id="${escapeAttribute(field.field_id)}">
        <label>
          <span>字段名称</span>
          <input value="${escapeAttribute(field.name || "")}" data-matrix-field-name>
        </label>
        <label>
          <span>判断依据和格式要求</span>
          <textarea data-matrix-field-rule>${escapeHtml(field.rule || "")}</textarea>
        </label>
        <button class="icon-action-btn danger" type="button" data-delete-matrix-field title="删除字段">×</button>
      </article>
    `).join("");
  };

  const appendMatrixFieldDrafts = (fields, sourceLabel = "AI 推荐") => {
    const drafts = (fields || []).filter((field) => field?.name || field?.rule);
    if (!fieldList || !drafts.length) return;
    drafts.forEach((field, index) => {
      const id = `field-${Date.now().toString(36)}-${index}`;
      fieldList.insertAdjacentHTML("beforeend", `
        <article class="matrix-field-card" data-field-id="${escapeAttribute(id)}">
          <label>
            <span>字段名称</span>
            <input value="${escapeAttribute(field.name || "自定义字段")}" data-matrix-field-name>
          </label>
          <label>
            <span>判断依据和格式要求</span>
            <textarea data-matrix-field-rule>${escapeHtml(field.rule || "请填写该字段的判断依据和格式要求。")}</textarea>
          </label>
          <small class="matrix-draft-source">${escapeHtml(sourceLabel)}，保存前可继续修改。</small>
          <button class="icon-action-btn danger" type="button" data-delete-matrix-field title="删除字段">×</button>
        </article>
      `);
    });
    setMatrixFieldsDirty(true);
    markMatrixDraftState();
  };

  const renderMatrixRecommendStatus = ({ status = "running", sourceCount = 0, events = [] } = {}) => {
    if (!statusBox) return;
    hiddenProgressKinds.delete("reading-matrix");
    const eventHtml = events.map((message) => `<div class="task-event-item">${escapeHtml(message)}</div>`).join("");
    statusBox.innerHTML = `
      <div class="matrix-progress-head">
        <strong>${status === "running" ? "正在推荐文献矩阵字段" : "AI 推荐字段已完成"}</strong>
        <span>关联 ${sourceCount} 篇文献</span>
        <button class="progress-close-btn" type="button" data-dismiss-progress aria-label="隐藏进度">×</button>
      </div>
      <div class="matrix-progress-bar"><span class="${status === "running" ? "is-indeterminate" : ""}" style="width:${status === "running" ? 68 : 100}%"></span></div>
      <div class="task-event-list">${eventHtml}</div>
    `;
  };

  const renderMatrixStatus = (task) => {
    if (!statusBox) return;
    if (hiddenProgressKinds.has("reading-matrix")) return;
    if (!task) {
      statusBox.innerHTML = "<span>先勾选已下载 PDF 的论文，再运行文献矩阵。</span>";
      return;
    }
    const total = Number(task.total || 0);
    const completed = Number(task.completed || 0);
    const failed = Number(task.failed || 0);
    const percent = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
    const events = Array.isArray(task.events)
      ? task.events.slice(-6).map((event) => `<div class="task-event-item">${escapeHtml(event.message || "")}</div>`).join("")
      : "";
    statusBox.innerHTML = `
      <div class="matrix-progress-head">
        <strong>${task.status === "running" ? "正在生成文献矩阵" : "文献矩阵任务已结束"}</strong>
        <span>${completed} / ${total}${failed ? `，失败 ${failed}` : ""}</span>
        <button class="progress-close-btn" type="button" data-dismiss-progress aria-label="隐藏进度">×</button>
      </div>
      <div class="matrix-progress-bar"><span style="width:${percent}%"></span></div>
      <div class="task-event-list" data-reading-matrix-events>${events}</div>
    `;
  };

  const setMatrixBusy = (busy) => {
    if (!runButton) return;
    if (busy) {
      runButton.classList.add("stop-send-btn");
      runButton.removeAttribute("data-run-reading-matrix");
      runButton.setAttribute("data-stop-reading-matrix", "");
      runButton.setAttribute("aria-label", "停止文献矩阵任务");
      runButton.setAttribute("title", "停止文献矩阵任务");
      runButton.innerHTML = '<span class="stop-icon"></span>';
    } else {
      runButton.classList.remove("stop-send-btn");
      runButton.removeAttribute("data-stop-reading-matrix");
      runButton.setAttribute("data-run-reading-matrix", "");
      runButton.setAttribute("aria-label", "运行文献矩阵");
      runButton.setAttribute("title", "运行文献矩阵");
      runButton.textContent = "➤";
    }
  };

  const updateMatrixChecks = (papers) => {
    (papers || []).forEach((paper) => {
      const cell = document.querySelector(`[data-matrix-cell="${CSS.escape(paper.paper_id || "")}"]`);
      if (cell && paper.has_structured_reading) {
        cell.innerHTML = '<span class="matrix-check" title="已生成文献矩阵">√</span>';
      }
    });
  };

  const pollReadingMatrixStatus = async () => {
    const statusUrl = readingMatrixPanel.dataset.statusUrl;
    if (!statusUrl) return;
    const response = await fetch(statusUrl, { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    renderMatrixStatus(payload.latest);
    updateMatrixChecks(payload.papers || []);
    if (!payload.running) {
      window.clearInterval(matrixPollTimer);
      matrixPollTimer = null;
      setMatrixBusy(false);
    }
  };

  const startMatrixPolling = () => {
    if (matrixPollTimer) return;
    matrixPollTimer = window.setInterval(pollReadingMatrixStatus, 3000);
  };

  fieldList?.addEventListener("input", (event) => {
    if (event.target.closest("[data-matrix-field-name], [data-matrix-field-rule]")) {
      markMatrixDraftState();
      setMatrixFieldsDirty(true);
    }
  });

  fieldList?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-delete-matrix-field]");
    if (!button) return;
    const cards = Array.from(fieldList?.querySelectorAll("[data-field-id]") || []);
    if (cards.length <= 1) {
      window.alert("至少保留一个文献矩阵字段。");
      return;
    }
    button.closest("[data-field-id]")?.remove();
    markMatrixDraftState();
    setMatrixFieldsDirty(true);
  });

  addButton?.addEventListener("click", async () => {
    appendMatrixFieldDrafts([{ name: "自定义字段", rule: "请填写该字段的判断依据和格式要求。" }], "手动新增");
  });

  recommendButton?.addEventListener("click", async () => {
    if (!readingMatrixPanel.dataset.recommendUrl) return;
    const paperIds = paperChecks.filter((item) => item.checked).map((item) => item.value);
    const sourceCount = matrixRecommendSourceCount();
    recommendButton.disabled = true;
    const originalText = recommendButton.textContent;
    recommendButton.textContent = "推荐中...";
    renderMatrixRecommendStatus({
      status: "running",
      sourceCount,
      events: [
        `已读取 ${sourceCount} 篇关联文献的标题、摘要、关键词和已有矩阵。`,
        "正在调用 AI 预分析综述写作需要的矩阵字段。",
      ],
    });
    try {
      const response = await fetch(readingMatrixPanel.dataset.recommendUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
        body: JSON.stringify({ paper_ids: paperIds }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "recommend fields failed");
      appendMatrixFieldDrafts(payload.fields || [], `AI 推荐字段，基于 ${payload.source_count || 0} 篇论文`);
      renderMatrixRecommendStatus({
        status: "success",
        sourceCount: payload.source_count || sourceCount,
        events: [
          `AI 已返回 ${payload.fields?.length || 0} 个字段建议。`,
          "建议字段已追加为草稿，请检查后点击保存字段。",
        ],
      });
    } catch (error) {
      renderMatrixRecommendStatus({
        status: "success",
        sourceCount,
        events: [`AI 推荐字段失败：${error.message || "未知错误"}`],
      });
      window.alert(error.message || "AI 推荐字段失败。");
    } finally {
      recommendButton.disabled = false;
      updateMatrixRecommendLabel();
    }
  });

  saveButton?.addEventListener("click", async () => {
    try {
      await saveMatrixFields();
    } catch (error) {
      window.alert(error.message || "保存文献矩阵字段失败。");
    }
  });

  if (matrixProjectId) {
    const draftKey = `guangming-matrix-field-drafts:${matrixProjectId}`;
    try {
      const payload = JSON.parse(window.localStorage.getItem(draftKey) || "null");
      if (payload?.fields?.length) {
        appendMatrixFieldDrafts(payload.fields, payload.source || "综述写作建议");
        readingMatrixPanel.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      window.localStorage.removeItem(draftKey);
    } catch (_error) {
      window.localStorage.removeItem(draftKey);
    }
  }

  paperChecks.forEach((item) => item.addEventListener("change", updateMatrixRecommendLabel));
  updateMatrixRecommendLabel();

  readingMatrixPanel.addEventListener("click", async (event) => {
    const stop = event.target.closest("[data-stop-reading-matrix]");
    if (stop) {
      event.preventDefault();
      stop.disabled = true;
      const response = await fetch(readingMatrixPanel.dataset.stopUrl, {
        method: "POST",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json();
      renderMatrixStatus(payload.latest);
      window.clearInterval(matrixPollTimer);
      matrixPollTimer = null;
      setMatrixBusy(false);
      stop.disabled = false;
      return;
    }
    const run = event.target.closest("[data-run-reading-matrix]");
    if (!run || run.classList.contains("stop-send-btn")) return;
    const paperIds = paperChecks.filter((item) => item.checked).map((item) => item.value);
    if (!paperIds.length) {
      window.alert("请先勾选要生成文献矩阵的论文。");
      return;
    }
    if (matrixFieldsDirty) {
      const saved = await saveMatrixFields();
      if (!saved) return;
    }
    const overwrite = window.confirm("请选择运行策略：确定 = 覆盖重跑已有文献矩阵；取消 = 跳过已生成文献矩阵。");
    hiddenProgressKinds.delete("reading-matrix");
    setMatrixBusy(true);
    try {
      const response = await fetch(readingMatrixPanel.dataset.runUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
        body: JSON.stringify({
          paper_ids: paperIds,
          mode: overwrite ? "overwrite_existing" : "skip_existing",
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "matrix run failed");
      renderMatrixStatus(payload.task);
      startMatrixPolling();
    } catch (error) {
      setMatrixBusy(false);
      window.alert(error.message || "启动文献矩阵任务失败。");
    }
  });

  if (readingMatrixPanel.querySelector("[data-stop-reading-matrix]")) {
    setMatrixBusy(true);
    startMatrixPolling();
  }
}

const bibtexButton = document.querySelector("[data-bibtex-export]");
if (bibtexButton) {
  const bibtexStatusBox = document.querySelector("[data-bibtex-status-box]");
  let bibtexPollTimer = null;
  let bibtexDownloaded = false;

  const renderBibtexStatus = (task) => {
    if (!bibtexStatusBox) return;
    if (hiddenProgressKinds.has("bibtex")) return;
    if (!task) {
      bibtexStatusBox.classList.remove("is-active");
      bibtexStatusBox.innerHTML = "";
      return;
    }
    bibtexStatusBox.classList.add("is-active");
    const total = Number(task.total || 0);
    const completed = Number(task.completed || 0);
    const failed = Number(task.failed || 0);
    const skipped = Number(task.skipped || 0);
    const percent = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
    const events = Array.isArray(task.events)
      ? task.events.slice(-6).map((event) => `<div class="task-event-item">${escapeHtml(event.message || "")}</div>`).join("")
      : "";
    bibtexStatusBox.innerHTML = `
      <div class="matrix-progress-head">
        <strong>${task.status === "running" ? "正在补全并导出 BibTeX" : "BibTeX 任务已结束"}</strong>
        <span>${completed} / ${total}${failed ? `，失败 ${failed}` : ""}${skipped ? `，跳过 ${skipped}` : ""}</span>
        <button class="progress-close-btn" type="button" data-dismiss-progress aria-label="隐藏进度">×</button>
      </div>
      <div class="matrix-progress-bar"><span style="width:${percent}%"></span></div>
      <div class="task-event-list" data-bibtex-events>${events}</div>
    `;
  };

  const updateBibtexCells = (papers) => {
    (papers || []).forEach((paper) => {
      const cell = document.querySelector(`[data-bibtex-cell="${CSS.escape(paper.paper_id || "")}"]`);
      if (cell) cell.textContent = paper.bibtex_status || "未生成";
    });
  };

  const setBibtexBusy = (busy) => {
    bibtexButton.disabled = false;
    bibtexButton.textContent = busy ? "停止 BibTeX 任务" : "BibTeX 补全并导出";
    bibtexButton.classList.toggle("danger", busy);
  };

  const pollBibtexStatus = async () => {
    if (!bibtexButton.dataset.bibtexStatusUrl) return;
    const response = await fetch(bibtexButton.dataset.bibtexStatusUrl, { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    renderBibtexStatus(payload.latest);
    updateBibtexCells(payload.papers || []);
    if (!payload.running) {
      window.clearInterval(bibtexPollTimer);
      bibtexPollTimer = null;
      setBibtexBusy(false);
      if (payload.download_url && !bibtexDownloaded && payload.latest?.status === "success") {
        bibtexDownloaded = true;
        window.location.href = payload.download_url;
      }
    }
  };

  const startBibtexPolling = () => {
    if (bibtexPollTimer) return;
    bibtexPollTimer = window.setInterval(pollBibtexStatus, 2500);
  };

  bibtexButton.addEventListener("click", async () => {
    if (bibtexButton.classList.contains("danger")) {
      const response = await fetch(bibtexButton.dataset.bibtexStopUrl, {
        method: "POST",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json();
      renderBibtexStatus(payload.latest);
      window.clearInterval(bibtexPollTimer);
      bibtexPollTimer = null;
      setBibtexBusy(false);
      return;
    }

    const paperIds = paperChecks.filter((item) => item.checked).map((item) => item.value);
    if (!paperIds.length) {
      window.alert("请先勾选要导出的文献。");
      return;
    }
    bibtexDownloaded = false;
    hiddenProgressKinds.delete("bibtex");
    setBibtexBusy(true);
    try {
      const response = await fetch(bibtexButton.dataset.bibtexRunUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
        body: JSON.stringify({ paper_ids: paperIds }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "bibtex run failed");
      renderBibtexStatus(payload.task);
      startBibtexPolling();
    } catch (error) {
      setBibtexBusy(false);
      window.alert(error.message || "启动 BibTeX 任务失败。");
    }
  });

  if (bibtexStatusBox?.classList.contains("is-active")) {
    setBibtexBusy(true);
    startBibtexPolling();
  }
}

const readingWorkbench = document.querySelector("[data-reading-workbench]");
if (readingWorkbench) {
  const sidePanel = readingWorkbench.querySelector("[data-reading-side-panel]");
  const tabButtons = Array.from(readingWorkbench.querySelectorAll("[data-reading-tab]"));
  const tabPanels = Array.from(readingWorkbench.querySelectorAll("[data-reading-tab-panel]"));
  const splitStoreKey = "guangming-reading-split-widths";
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  const applyReadingSplit = (leftWidth, rightWidth) => {
    if (leftWidth) readingWorkbench.style.setProperty("--reading-left-width", `${Math.round(leftWidth)}px`);
    if (rightWidth) readingWorkbench.style.setProperty("--reading-right-width", `${Math.round(rightWidth)}px`);
  };

  const readCurrentSplit = () => {
    const styles = window.getComputedStyle(readingWorkbench);
    const left = Number.parseFloat(styles.getPropertyValue("--reading-left-width")) || sidePanel?.getBoundingClientRect().width || 330;
    const right = Number.parseFloat(styles.getPropertyValue("--reading-right-width")) || readingWorkbench.querySelector(".reading-chat-pane")?.getBoundingClientRect().width || 390;
    return { left, right };
  };

  try {
    const saved = JSON.parse(window.localStorage.getItem(splitStoreKey) || "{}");
    if (saved.left || saved.right) applyReadingSplit(saved.left, saved.right);
  } catch (_error) {
    // Ignore invalid localStorage content.
  }
  window.requestAnimationFrame(() => {
    const rect = readingWorkbench.getBoundingClientRect();
    const current = readCurrentSplit();
    const left = clamp(current.left, 220, Math.max(240, rect.width - current.right - 520));
    const right = clamp(current.right, 280, Math.max(280, rect.width - left - 520));
    applyReadingSplit(left, right);
  });

  tabButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.readingTab;
      if (!tab) return;
      tabButtons.forEach((item) => item.classList.toggle("active", item.dataset.readingTab === tab));
      tabPanels.forEach((panel) => panel.classList.toggle("active", panel.dataset.readingTabPanel === tab));
      sidePanel?.classList.remove("is-collapsed");
    });
  });

  readingWorkbench.querySelector("[data-reading-collapse]")?.addEventListener("click", () => {
    sidePanel?.classList.toggle("is-collapsed");
  });

  readingWorkbench.querySelectorAll("[data-reading-resizer]").forEach((resizer) => {
    resizer.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const mode = resizer.dataset.readingResizer;
      const start = readCurrentSplit();
      const rect = readingWorkbench.getBoundingClientRect();
      const maxLeft = Math.max(240, rect.width - start.right - 520);
      const maxRight = Math.max(280, rect.width - start.left - 520);
      readingWorkbench.classList.add("is-resizing");
      resizer.setPointerCapture?.(event.pointerId);

      const onMove = (moveEvent) => {
        if (mode === "left") {
          const nextLeft = clamp(moveEvent.clientX - rect.left, 220, maxLeft);
          applyReadingSplit(nextLeft, start.right);
          return;
        }
        const nextRight = clamp(rect.right - moveEvent.clientX, 280, maxRight);
        applyReadingSplit(start.left, nextRight);
      };

      const onUp = () => {
        readingWorkbench.classList.remove("is-resizing");
        const current = readCurrentSplit();
        window.localStorage.setItem(splitStoreKey, JSON.stringify(current));
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    });
  });
}

const pdfViewer = document.querySelector("[data-pdf-viewer]");
if (pdfViewer && pdfViewer.dataset.pdfUrl) {
  const pagesContainer = pdfViewer.querySelector("[data-pdf-pages-container]");
  const outlineBox = document.querySelector("[data-pdf-outline]");
  const pageText = pdfViewer.querySelector("[data-pdf-page]");
  const pagesText = pdfViewer.querySelector("[data-pdf-pages]");
  const scaleText = pdfViewer.querySelector("[data-pdf-scale]");
  const wrap = pdfViewer.querySelector("[data-pdf-canvas-wrap]");
  let pdfDocument = null;
  let currentPage = 1;
  let scale = 1.08;
  let renderVersion = 0;
  let observer = null;
  const renderingPages = new Set();

  const updatePdfState = () => {
    if (pageText) pageText.textContent = String(currentPage);
    if (pagesText) pagesText.textContent = String(pdfDocument?.numPages || "?");
    if (scaleText) scaleText.textContent = `${Math.round(scale * 100)}%`;
  };

  const pageShell = (pageNumber) => pagesContainer?.querySelector(`[data-pdf-page-shell="${pageNumber}"]`);

  const renderPageCanvas = async (pageNumber) => {
    if (!pdfDocument || !pagesContainer || renderingPages.has(pageNumber)) return;
    const shell = pageShell(pageNumber);
    const canvas = shell?.querySelector("canvas");
    if (!shell || !canvas || canvas.dataset.renderScale === String(scale)) return;
    renderingPages.add(pageNumber);
    const version = renderVersion;
    try {
      const page = await pdfDocument.getPage(pageNumber);
      if (version !== renderVersion) return;
      const viewport = page.getViewport({ scale });
      const context = canvas.getContext("2d");
      const outputScale = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;
      context.setTransform(outputScale, 0, 0, outputScale, 0, 0);
      await page.render({ canvasContext: context, viewport }).promise;
      canvas.dataset.renderScale = String(scale);
      shell.classList.add("is-rendered");
    } finally {
      renderingPages.delete(pageNumber);
    }
  };

  const renderNearbyPages = (pageNumber) => {
    [pageNumber - 1, pageNumber, pageNumber + 1, pageNumber + 2]
      .filter((item) => item >= 1 && item <= (pdfDocument?.numPages || 0))
      .forEach((item) => renderPageCanvas(item));
  };

  const scrollToPage = async (pageNumber) => {
    if (!pdfDocument) return;
    currentPage = Math.min(Math.max(1, pageNumber), pdfDocument.numPages);
    updatePdfState();
    await renderPageCanvas(currentPage);
    pageShell(currentPage)?.scrollIntoView({ behavior: "smooth", block: "start" });
    renderNearbyPages(currentPage);
  };

  const buildPageShells = () => {
    if (!pagesContainer || !pdfDocument) return;
    pagesContainer.innerHTML = Array.from({ length: pdfDocument.numPages }, (_, index) => {
      const pageNumber = index + 1;
      return `
        <article class="pdf-page-shell" data-pdf-page-shell="${pageNumber}">
          <canvas></canvas>
          <div class="pdf-page-label">${pageNumber}</div>
        </article>
      `;
    }).join("");
  };

  const setupPageObserver = () => {
    if (!pagesContainer || !wrap) return;
    observer?.disconnect();
    observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const pageNumber = Number(entry.target.dataset.pdfPageShell || 1);
        renderNearbyPages(pageNumber);
      });
      if (visible) {
        currentPage = Number(visible.target.dataset.pdfPageShell || currentPage);
        updatePdfState();
      }
    }, { root: wrap, threshold: [0.08, 0.25, 0.55] });
    pagesContainer.querySelectorAll("[data-pdf-page-shell]").forEach((shell) => observer.observe(shell));
  };

  const rerenderVisiblePages = () => {
    renderVersion += 1;
    pagesContainer?.querySelectorAll("canvas").forEach((canvas) => {
      canvas.dataset.renderScale = "";
    });
    renderNearbyPages(currentPage);
    window.setTimeout(() => scrollToPage(currentPage), 80);
  };

  const pageFromDestination = async (dest) => {
    const destination = Array.isArray(dest) ? dest : await pdfDocument.getDestination(dest);
    if (!destination?.length) return null;
    const ref = destination[0];
    const index = await pdfDocument.getPageIndex(ref);
    return index + 1;
  };

  const renderOutlineItems = (items) => (items || []).map((item) => {
    const childHtml = item.items?.length ? `<div class="pdf-outline-children">${renderOutlineItems(item.items)}</div>` : "";
    const hasChildren = Boolean(item.items?.length);
    return `
      <details class="pdf-outline-node" ${hasChildren ? "open" : ""}>
        <summary>
          <span class="outline-arrow">${hasChildren ? "▸" : ""}</span>
          <button type="button" class="pdf-outline-item" data-pdf-dest="${escapeAttribute(JSON.stringify(item.dest || ""))}">
            ${escapeHtml(item.title || "未命名目录")}
          </button>
        </summary>
        ${childHtml}
      </details>
    `;
  }).join("");

  const initOutline = async () => {
    if (!outlineBox || !pdfDocument) return;
    const outline = await pdfDocument.getOutline();
    if (!outline?.length) {
      outlineBox.innerHTML = '<div class="empty-state compact">该 PDF 未提供目录。</div>';
      return;
    }
    outlineBox.innerHTML = renderOutlineItems(outline);
    outlineBox.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-pdf-dest]");
      if (!button) return;
      event.preventDefault();
      try {
        const rawDest = JSON.parse(button.dataset.pdfDest || "\"\"");
        const pageNumber = await pageFromDestination(rawDest);
        if (pageNumber) await scrollToPage(pageNumber);
      } catch (_error) {
        // Ignore broken PDF outline entries.
      }
    });
  };

  (async () => {
    try {
      const pdfjsLib = await import(pdfViewer.dataset.pdfModuleUrl);
      pdfjsLib.GlobalWorkerOptions.workerSrc = pdfViewer.dataset.pdfWorkerUrl;
      pdfDocument = await pdfjsLib.getDocument({
        url: pdfViewer.dataset.pdfUrl,
        standardFontDataUrl: pdfViewer.dataset.standardFontUrl,
      }).promise;
      buildPageShells();
      setupPageObserver();
      updatePdfState();
      renderNearbyPages(1);
      await initOutline();
    } catch (error) {
      if (wrap) {
        wrap.innerHTML = `<div class="pdf-empty"><strong>PDF 加载失败</strong><p>${escapeHtml(error.message || "无法打开当前 PDF。")}</p></div>`;
      }
      if (outlineBox) outlineBox.innerHTML = '<div class="empty-state compact">PDF 加载失败，无法读取目录。</div>';
    }
  })();

  pdfViewer.querySelector("[data-pdf-zoom-in]")?.addEventListener("click", () => {
    scale = Math.min(2.2, scale + 0.12);
    updatePdfState();
    rerenderVisiblePages();
  });
  pdfViewer.querySelector("[data-pdf-zoom-out]")?.addEventListener("click", () => {
    scale = Math.max(0.62, scale - 0.12);
    updatePdfState();
    rerenderVisiblePages();
  });

  let screenshotMode = false;
  let selectionBox = null;
  let selectionStart = null;

  const stopScreenshotMode = () => {
    screenshotMode = false;
    pdfViewer.classList.remove("is-screenshotting");
    selectionBox?.remove();
    selectionBox = null;
    selectionStart = null;
  };

  const startScreenshotMode = () => {
    screenshotMode = true;
    pdfViewer.classList.add("is-screenshotting");
  };

  const cropCanvasRegion = async (canvas, crop) => new Promise((resolve) => {
    const target = document.createElement("canvas");
    target.width = Math.max(1, Math.round(crop.width));
    target.height = Math.max(1, Math.round(crop.height));
    const context = target.getContext("2d");
    context.drawImage(canvas, crop.x, crop.y, crop.width, crop.height, 0, 0, target.width, target.height);
    target.toBlob((blob) => resolve(blob), "image/png");
  });

  document.addEventListener("guangming:start-pdf-screenshot", startScreenshotMode);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && screenshotMode) stopScreenshotMode();
  });

  wrap?.addEventListener("pointerdown", (event) => {
    if (!screenshotMode) return;
    const shell = event.target.closest("[data-pdf-page-shell]");
    const canvas = shell?.querySelector("canvas");
    if (!shell || !canvas || !canvas.width || !canvas.height) return;
    event.preventDefault();
    const shellRect = shell.getBoundingClientRect();
    selectionStart = {
      shell,
      canvas,
      x: event.clientX - shellRect.left,
      y: event.clientY - shellRect.top,
    };
    selectionBox?.remove();
    selectionBox = document.createElement("div");
    selectionBox.className = "pdf-screenshot-selection";
    shell.appendChild(selectionBox);
    shell.setPointerCapture?.(event.pointerId);
  });

  wrap?.addEventListener("pointermove", (event) => {
    if (!selectionStart || !selectionBox) return;
    const shellRect = selectionStart.shell.getBoundingClientRect();
    const x = event.clientX - shellRect.left;
    const y = event.clientY - shellRect.top;
    const left = Math.min(selectionStart.x, x);
    const top = Math.min(selectionStart.y, y);
    const width = Math.abs(x - selectionStart.x);
    const height = Math.abs(y - selectionStart.y);
    Object.assign(selectionBox.style, {
      left: `${left}px`,
      top: `${top}px`,
      width: `${width}px`,
      height: `${height}px`,
    });
  });

  wrap?.addEventListener("pointerup", async (event) => {
    if (!selectionStart || !selectionBox) return;
    const boxRect = selectionBox.getBoundingClientRect();
    const canvasRect = selectionStart.canvas.getBoundingClientRect();
    const width = Math.min(boxRect.right, canvasRect.right) - Math.max(boxRect.left, canvasRect.left);
    const height = Math.min(boxRect.bottom, canvasRect.bottom) - Math.max(boxRect.top, canvasRect.top);
    if (width < 12 || height < 12) {
      stopScreenshotMode();
      return;
    }
    const scaleX = selectionStart.canvas.width / canvasRect.width;
    const scaleY = selectionStart.canvas.height / canvasRect.height;
    const crop = {
      x: (Math.max(boxRect.left, canvasRect.left) - canvasRect.left) * scaleX,
      y: (Math.max(boxRect.top, canvasRect.top) - canvasRect.top) * scaleY,
      width: width * scaleX,
      height: height * scaleY,
    };
    const blob = await cropCanvasRegion(selectionStart.canvas, crop);
    stopScreenshotMode();
    if (!blob) return;
    const file = new File([blob], `pdf-screenshot-${Date.now()}.png`, { type: "image/png" });
    document.dispatchEvent(new CustomEvent("guangming:reading-attachment", { detail: { file } }));
  });
}

const readingChatForm = document.querySelector("[data-reading-chat-form]");
if (readingChatForm) {
  const textarea = readingChatForm.querySelector("textarea");
  const submitButton = readingChatForm.querySelector(".reading-chat-controls .send-btn");
  const screenshotButton = readingChatForm.querySelector("[data-start-reading-screenshot]");
  const compactButton = readingChatForm.querySelector("[data-compact-reading-chat]");
  const attachmentTray = readingChatForm.querySelector("[data-reading-attachment-tray]");
  const chatWindow = document.querySelector("[data-reading-chat-window]");
  let readingChatPollTimer = null;
  let readingAttachments = [];

  const scrollReadingChatToBottom = () => {
    if (chatWindow) chatWindow.scrollTop = chatWindow.scrollHeight;
  };

  const renderReadingMessage = (message) => {
    if (message.role === "divider") {
      return `<div class="chat-divider"><span>${escapeHtml(message.content || "新的对话")}</span></div>`;
    }
    const isUser = message.role === "user";
    const attachmentHtml = Array.isArray(message.attachments) && message.attachments.length
      ? `<div class="chat-attachment-row">${message.attachments
          .filter((attachment) => attachment.type === "image" && attachment.url)
          .map((attachment) => `
            <a class="chat-image-thumb" href="${escapeAttribute(attachment.url)}" target="_blank" title="查看截图">
              <img src="${escapeAttribute(attachment.url)}" alt="用户附加截图">
            </a>
          `).join("")}</div>`
      : "";
    const content = isUser
      ? `${attachmentHtml}<p>${escapeHtml(message.content || "")}</p>`
      : `<div class="markdown-body">${renderMarkdown(message.content || "")}</div>`;
    return `
      <article class="chat-message ${isUser ? "user" : "assistant"}">
        <div class="chat-avatar">${isUser ? "我" : "光"}</div>
        <div class="chat-bubble">
          <div class="chat-meta">
            <span>${isUser ? "我的问题" : "论文研读"}</span>
            <time>${escapeHtml(message.created_at || "")}</time>
          </div>
          ${content}
        </div>
      </article>
    `;
  };

  const renderReadingRunningMessage = (task) => {
    if (!task) return "";
    const events = Array.isArray(task.events)
      ? task.events.slice(-6).map((event) => `<div class="task-event-item">${escapeHtml(event.message || "")}</div>`).join("")
      : "";
    return `
      <article class="chat-message assistant running">
        <div class="chat-avatar">光</div>
        <div class="chat-bubble">
          <div class="chat-meta">
            <span>论文研读</span>
            <time>${escapeHtml(task.started_at || "")}</time>
          </div>
          <p>正在围绕当前论文回答问题。</p>
          <div class="chat-result-line loading-line">
            <span class="loading-dot"></span>
            问答中，请稍候
          </div>
          <div class="task-event-list">${events}</div>
        </div>
      </article>
    `;
  };

  const renderReadingChat = (messages, task = null) => {
    if (!chatWindow) return;
    if ((!messages || !messages.length) && !task) {
      chatWindow.innerHTML = `
        <div class="chat-empty">
          <strong>开始和这篇论文对话</strong>
          <span>可以问“这篇论文的核心贡献是什么？”、“实验设置怎么理解？”或“它适合放在综述哪一节？”。</span>
        </div>
      `;
      return;
    }
    chatWindow.innerHTML = [
      ...(messages || []).map(renderReadingMessage),
      task && task.status === "running" ? renderReadingRunningMessage(task) : "",
    ].join("");
    chatWindow.querySelectorAll(".markdown-body a").forEach((link) => {
      link.target = "_blank";
      link.rel = "noreferrer";
    });
    scrollReadingChatToBottom();
  };

  const setReadingChatBusy = (busy) => {
    if (submitButton) {
      submitButton.disabled = false;
      if (busy) {
        submitButton.type = "button";
        submitButton.classList.add("stop-send-btn");
        submitButton.setAttribute("data-stop-reading-chat", "");
        submitButton.setAttribute("aria-label", "停止本次问答");
        submitButton.setAttribute("title", "停止本次问答");
        submitButton.innerHTML = '<span class="stop-icon"></span>';
      } else {
        submitButton.type = "submit";
        submitButton.classList.remove("stop-send-btn");
        submitButton.removeAttribute("data-stop-reading-chat");
        submitButton.setAttribute("aria-label", "发送论文问题");
        submitButton.setAttribute("title", "发送论文问题");
        submitButton.textContent = "➤";
      }
    }
    if (textarea) textarea.readOnly = busy;
    if (screenshotButton) screenshotButton.disabled = busy;
    if (compactButton) compactButton.disabled = busy;
  };

  const renderReadingAttachments = () => {
    if (!attachmentTray) return;
    attachmentTray.classList.toggle("is-active", readingAttachments.length > 0);
    attachmentTray.innerHTML = readingAttachments.map((attachment, index) => `
      <div class="reading-attachment-thumb">
        <img src="${escapeAttribute(attachment.previewUrl)}" alt="待发送截图">
        <button type="button" data-remove-reading-attachment="${index}" aria-label="删除截图">×</button>
      </div>
    `).join("");
  };

  const addReadingAttachment = (file) => {
    if (!file || !file.type?.startsWith("image/")) return;
    if (readingAttachments.length >= 6) {
      window.alert("一次最多附加 6 张图片。");
      return;
    }
    readingAttachments.push({
      file,
      previewUrl: URL.createObjectURL(file),
    });
    renderReadingAttachments();
  };

  const clearReadingAttachments = () => {
    readingAttachments.forEach((attachment) => URL.revokeObjectURL(attachment.previewUrl));
    readingAttachments = [];
    renderReadingAttachments();
  };

  attachmentTray?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-reading-attachment]");
    if (!button) return;
    const index = Number(button.dataset.removeReadingAttachment);
    const [removed] = readingAttachments.splice(index, 1);
    if (removed) URL.revokeObjectURL(removed.previewUrl);
    renderReadingAttachments();
  });

  screenshotButton?.addEventListener("click", () => {
    document.dispatchEvent(new CustomEvent("guangming:start-pdf-screenshot"));
  });

  document.addEventListener("guangming:reading-attachment", (event) => {
    addReadingAttachment(event.detail?.file);
  });

  textarea?.addEventListener("paste", (event) => {
    const items = Array.from(event.clipboardData?.items || []);
    const imageItems = items.filter((item) => item.type.startsWith("image/"));
    if (!imageItems.length) return;
    event.preventDefault();
    imageItems.forEach((item) => {
      const file = item.getAsFile();
      if (file) addReadingAttachment(file);
    });
  });

  const startReadingChatPolling = () => {
    if (readingChatPollTimer) return;
    readingChatPollTimer = window.setInterval(pollReadingChatStatus, 3000);
  };

  const stopReadingChatPolling = () => {
    if (!readingChatPollTimer) return;
    window.clearInterval(readingChatPollTimer);
    readingChatPollTimer = null;
  };

  async function pollReadingChatStatus() {
    const statusUrl = readingChatForm.dataset.statusUrl;
    if (!statusUrl) return;
    try {
      const response = await fetch(statusUrl, { cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      renderReadingChat(payload.messages || [], payload.running ? payload.latest : null);
      if (!payload.running) {
        stopReadingChatPolling();
        setReadingChatBusy(false);
      }
    } catch (_error) {
      // Let the next polling cycle retry.
    }
  }

  readingChatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = textarea?.value.trim() || "";
    if (!question && !readingAttachments.length) {
      window.alert("请输入论文研读问题，或先截图/粘贴一张图片。");
      return;
    }
    setReadingChatBusy(true);
    try {
      const formData = new FormData();
      formData.append("user_question", question);
      readingAttachments.forEach((attachment) => {
        formData.append("images", attachment.file, attachment.file.name || `reading-image-${Date.now()}.png`);
      });
      const response = await fetch(readingChatForm.action, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
        body: formData,
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "submit failed");
      if (textarea) textarea.value = "";
      clearReadingAttachments();
      renderReadingChat(payload.messages || [payload.user_message].filter(Boolean), {
        run_id: payload.run_id,
        status: "running",
        started_at: "",
        events: [{ message: "论文研读问答任务已提交。" }],
      });
      startReadingChatPolling();
    } catch (error) {
      window.alert(error.message || "论文研读问答提交失败。");
      setReadingChatBusy(false);
    }
  });

  readingChatForm.addEventListener("click", async (event) => {
    const stopButton = event.target.closest("[data-stop-reading-chat]");
    if (!stopButton) return;
    event.preventDefault();
    stopButton.disabled = true;
    try {
      const response = await fetch(readingChatForm.dataset.stopUrl, {
        method: "POST",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "stop failed");
      stopReadingChatPolling();
      setReadingChatBusy(false);
      renderReadingChat(payload.messages || []);
    } catch (error) {
      window.alert(error.message || "停止论文研读问答失败。");
      stopButton.disabled = false;
    }
  });

  compactButton?.addEventListener("click", async () => {
    if (!window.confirm("确定要压缩当前论文研读对话记忆吗？压缩完成后会继续沿用当前线程。")) return;
    const originalText = compactButton.textContent;
    compactButton.disabled = true;
    compactButton.textContent = "压缩中...";
    try {
      const response = await fetch(readingChatForm.dataset.compactUrl, {
        method: "POST",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "compact failed");
      stopReadingChatPolling();
      setReadingChatBusy(false);
      renderReadingChat(payload.messages || []);
    } catch (error) {
      window.alert(error.message || "压缩论文研读对话记忆失败。");
    } finally {
      compactButton.disabled = false;
      compactButton.textContent = originalText || "压缩记忆";
    }
  });

  readingChatForm.querySelector("[data-reset-reading-chat]")?.addEventListener("click", async () => {
    if (!window.confirm("确定要重置当前论文的研读对话吗？这会开启新的对话线程，但保留历史分割线。")) return;
    try {
      const response = await fetch(readingChatForm.dataset.resetUrl, {
        method: "POST",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "reset failed");
      stopReadingChatPolling();
      setReadingChatBusy(false);
      renderReadingChat(payload.messages || []);
    } catch (error) {
      window.alert(error.message || "重置论文研读对话失败。");
    }
  });

  if (document.querySelector(".reading-chat-pane .chat-message.running")) {
    setReadingChatBusy(true);
    startReadingChatPolling();
  }
  scrollReadingChatToBottom();
}

const writingWorkbench = document.querySelector("[data-writing-workbench]");
if (writingWorkbench) {
  const stageOrder = ["topic", "outline", "mapping", "draft"];
  const stageStrip = document.querySelector("[data-writing-stage-strip]");
  const stageUrl = stageStrip?.dataset.stageUrl || "";
  const stageNodes = Array.from(document.querySelectorAll("[data-writing-stage]"));
  const stagePanels = Array.from(document.querySelectorAll("[data-writing-stage-panel]"));
  const paperList = writingWorkbench.querySelector("[data-writing-paper-list]");
  const matrixPreview = writingWorkbench.querySelector("[data-writing-matrix-preview]");
  const sectionMap = writingWorkbench.querySelector("[data-writing-section-map]");
  const outlineEditor = writingWorkbench.querySelector("[data-writing-outline]");
  const outlinePreview = writingWorkbench.querySelector("[data-writing-outline-preview]");
  const draftEditor = writingWorkbench.querySelector("[data-writing-draft]");
  const draftPreview = writingWorkbench.querySelector("[data-writing-draft-preview]");
  const chatForm = writingWorkbench.querySelector("[data-writing-chat-form]");
  const chatWindow = writingWorkbench.querySelector("[data-writing-chat-window]");
  const quickRow = writingWorkbench.querySelector("[data-writing-quick-row]");
  const chatTextarea = chatForm?.querySelector("textarea");
  const chatSubmitButton = chatForm?.querySelector(".writing-chat-controls .send-btn");
  const compactButton = chatForm?.querySelector("[data-compact-writing-chat]");
  const matrixByPaper = JSON.parse(writingWorkbench.dataset.matrix || "{}");
  let writingMapping = JSON.parse(writingWorkbench.dataset.writingMapping || "{\"sections\":[],\"papers\":[],\"mappings\":[]}");
  const writingProjectId = writingWorkbench.dataset.projectId || "";
  const writingTopicUrl = writingWorkbench.dataset.topicUrl || "";
  const splitStoreKey = "guangming-writing-split-width";
  let currentStage = writingWorkbench.dataset.currentStage || "topic";
  let writingPollTimer = null;

  const writingStagePrompts = {
    topic: {
      placeholder: "这个阶段主要和 AI 讨论综述主题。可以说：我想突出机器人操作、双臂协同或 VLA 泛化能力，也可以让 AI 判断当前文献够不够支撑主题。",
      quicks: [
        ["拟定主题", "请基于当前已选文献和文献矩阵，帮我拟定一个合适的文献综述主题，并说明为什么这个主题适合当前材料。"],
        ["比较主题方向", "请给我比较 3-4 个可选综述主题方向，说明每个方向的覆盖范围、风险和适合的写作角度。"],
        ["判断文献是否够", "请判断当前已选文献是否足够支撑一个完整综述主题；如果不够，请明确缺口，并给出可复制到文献检索页的完整检索要求。"],
        ["推荐矩阵字段", "请基于当前文献和拟定主题，判断是否需要新增文献矩阵字段；如果需要，请说明原因、字段名、判断依据和格式要求。"],
      ],
    },
    outline: {
      placeholder: "这个阶段主要打磨大纲。可以说明目标篇幅、课程报告或学术综述风格、希望突出的方法链路，以及你想采用的一二级结构。",
      quicks: [
        ["短篇大纲", "请基于当前主题、CSV 和已有大纲，生成一版短篇综述大纲，适合 3000-5000 字，包含一级和二级标题。"],
        ["中篇大纲", "请生成一版中篇综述大纲，适合 6000-9000 字，要求结构清晰、章节之间有递进关系，并说明每章写作重点。"],
        ["长篇大纲", "请生成一版长篇综述大纲，适合 10000 字以上，要求覆盖研究背景、方法分类、实验评估、应用场景、挑战与展望。"],
        ["课程报告大纲", "请按课程报告风格优化当前大纲，要求逻辑清楚、重点突出、篇幅可控，并给出每节建议字数。"],
        ["学术综述大纲", "请按正式学术综述风格优化当前大纲，突出分类体系、研究脉络、关键问题和未来方向。"],
      ],
    },
    mapping: {
      placeholder: "这个阶段主要核对每个章节该引用哪些论文。可以让 AI 分配文献、指出缺少证据的章节，或补写每篇论文在对应章节中的写作备注。",
      quicks: [
        ["分配文献", "请根据当前大纲、writing_sources.csv、文献矩阵和每篇论文的 paper_dir，逐小节分配文献，并生成每篇文献在对应小节的写作内容备注、证据细节和缺失细节。"],
        ["检查缺口", "请检查当前小节-文献映射是否存在证据不足、章节空洞或文献过度集中的问题，并给出补充检索建议。"],
        ["生成写作备注", "请围绕当前小节-文献映射补强写作内容备注，尽量指出具体方法、实验细节、数据或仍需从 paper_dir 补查的内容。"],
        ["优化引用布局", "请优化各小节引用布局，避免同一篇文献被过度使用，同时保证核心章节有足够代表性论文支撑。"],
      ],
    },
    draft: {
      placeholder: "这个阶段主要生成或修改本地 survey.md。可以说明要先写哪一节、目标字数、引用风格，或要求 AI 直接更新 Markdown 正文。",
      quicks: [
        ["开始撰写综述", "请基于当前主题、CSV、文献矩阵和大纲，开始撰写本地 survey.md。不要在聊天框输出完整正文，只说明写入了哪些部分。"],
        ["生成引言", "请先撰写 survey.md 的引言部分，要求说明研究背景、问题动机、综述范围和本文结构。"],
        ["生成相关工作", "请根据当前大纲和文献分配，撰写相关工作与方法分类部分，并使用数字引用格式。"],
        ["润色全文", "请检查并润色当前 survey.md，重点优化段落衔接、学术表达、引用位置和章节过渡。"],
        ["补参考文献", "请检查 survey.md 末尾参考文献列表，确保正文数字引用和参考文献条目一致。"],
      ],
    },
  };

  const updateWritingStageHelpers = () => {
    const config = writingStagePrompts[currentStage] || writingStagePrompts.topic;
    if (chatTextarea) chatTextarea.placeholder = config.placeholder;
    if (quickRow) {
      quickRow.innerHTML = config.quicks.map(([label, prompt]) => `
        <button type="button" data-writing-quick="${escapeAttribute(prompt)}">${escapeHtml(label)}</button>
      `).join("");
    }
  };

  const activePaperIds = () => Array.from(paperList?.querySelectorAll("input:checked") || []).map((input) => input.value);

  const setWritingStage = async (stage, persist = true) => {
    if (!stageOrder.includes(stage)) return;
    currentStage = stage;
    writingWorkbench.dataset.currentStage = stage;
    stageNodes.forEach((node) => node.classList.toggle("active", node.dataset.writingStage === stage));
    stagePanels.forEach((panel) => panel.classList.toggle("active", panel.dataset.writingStagePanel === stage));
    updateWritingStageHelpers();
    if (persist && stageUrl) {
      await fetch(stageUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "fetch" },
        body: JSON.stringify({ stage }),
      });
    }
  };

  const renderWritingMatrix = (paperId) => {
    if (!matrixPreview) return;
    const values = matrixByPaper[paperId] || {};
    const entries = Object.entries(values);
    if (!entries.length) {
      matrixPreview.innerHTML = '<div class="empty-state compact">该论文尚未生成文献矩阵。</div>';
      return;
    }
    matrixPreview.innerHTML = entries.map(([name, value]) => `
      <article class="reading-matrix-item">
        <span>${escapeHtml(name)}</span>
        <p>${escapeHtml(value || "尚未生成")}</p>
      </article>
    `).join("");
  };

  const renderWritingSectionMap = (mapping) => {
    if (!sectionMap || !mapping) return;
    writingMapping = mapping;
    const sections = Array.isArray(mapping.sections) ? mapping.sections : [];
    const mappings = Array.isArray(mapping.mappings) ? mapping.mappings : [];
    if (!sections.length) {
      sectionMap.innerHTML = '<div class="empty-state compact">当前大纲还没有可识别章节。请先在第二阶段生成并保存大纲。</div>';
      return;
    }
    sectionMap.innerHTML = sections.map((section) => `
      <article class="writing-section-card" data-outline-section="${escapeAttribute(section.title || "")}">
        <div class="writing-section-title-row">
          <h3>${escapeHtml(section.title || "未命名章节")}</h3>
          <span>${mappings.filter((row) => row.section_id === section.section_id).length} 篇文献</span>
        </div>
        ${(() => {
          const rows = mappings.filter((row) => row.section_id === section.section_id);
          if (!rows.length) return '<div class="empty-state compact">本小节尚未分配文献。运行“分配文献”后会在这里生成小节级写作备注。</div>';
          return rows.map((row) => `
            <div class="writing-map-row is-mapped" data-map-id="${escapeAttribute(row.mapping_id || "")}" data-map-section-id="${escapeAttribute(row.section_id || "")}" data-map-paper-id="${escapeAttribute(row.paper_id || "")}">
              <div class="writing-map-paper">
                <strong>${escapeHtml(row.paper_title || "")}</strong>
                <button class="icon-action-btn danger" type="button" data-delete-section-mapping title="移除该小节中的文献">×</button>
              </div>
              <label>
                <span>引用角色</span>
                <input data-map-field="citation_role" value="${escapeAttribute(row.citation_role || "")}" placeholder="核心证据 / 方法对比 / 实验支撑">
              </label>
              <label>
                <span>写作内容备注</span>
                <textarea data-map-field="writing_note" placeholder="这篇文献在本小节中具体写什么">${escapeHtml(row.writing_note || "")}</textarea>
              </label>
              <label>
                <span>证据细节</span>
                <textarea data-map-field="evidence_detail" placeholder="可写入正文的真实方法、实验、数据或论据细节">${escapeHtml(row.evidence_detail || "")}</textarea>
              </label>
              <label>
                <span>缺失细节</span>
                <textarea data-map-field="missing_detail" placeholder="仍需从 PDF 或资料补查的内容">${escapeHtml(row.missing_detail || "")}</textarea>
              </label>
            </div>
          `).join("");
        })()}
      </article>
    `).join("");
  };

  const saveWritingSelection = async (activePaperId = "") => {
    if (!paperList?.dataset.selectionUrl) return;
    const response = await fetch(paperList.dataset.selectionUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "fetch" },
      body: JSON.stringify({ paper_ids: activePaperIds(), active_paper_id: activePaperId }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "selection save failed");
  };

  paperList?.addEventListener("change", async (event) => {
    const input = event.target.closest("input[type='checkbox']");
    if (!input) return;
    try {
      await saveWritingSelection(input.value);
    } catch (error) {
      window.alert(error.message || "保存写作论文选择失败。");
    }
  });

  paperList?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-writing-paper-row]");
    if (!row) return;
    renderWritingMatrix(row.dataset.paperId);
  });

  const firstChecked = paperList?.querySelector("input:checked");
  if (firstChecked) renderWritingMatrix(firstChecked.value);

  stageNodes.forEach((node) => node.addEventListener("click", () => setWritingStage(node.dataset.writingStage)));
  document.querySelector("[data-writing-prev-stage]")?.addEventListener("click", () => {
    const index = Math.max(0, stageOrder.indexOf(currentStage) - 1);
    setWritingStage(stageOrder[index]);
  });
  document.querySelector("[data-writing-next-stage]")?.addEventListener("click", () => {
    const index = Math.min(stageOrder.length - 1, stageOrder.indexOf(currentStage) + 1);
    setWritingStage(stageOrder[index]);
  });

  const renderOutlinePreview = () => {
    if (!outlinePreview || !outlineEditor) return;
    outlinePreview.innerHTML = renderMarkdown(outlineEditor.value || "");
    outlinePreview.querySelectorAll("a").forEach((link) => {
      link.target = "_blank";
      link.rel = "noreferrer";
    });
  };

  const setOutlineMode = (mode) => {
    const preview = mode !== "edit";
    if (preview) renderOutlinePreview();
    if (outlineEditor) outlineEditor.hidden = preview;
    if (outlinePreview) outlinePreview.hidden = !preview;
  };

  document.querySelectorAll("[data-writing-outline-mode]").forEach((button) => {
    button.addEventListener("click", () => setOutlineMode(button.dataset.writingOutlineMode || "preview"));
  });

  document.querySelector("[data-save-writing-outline]")?.addEventListener("click", async () => {
    try {
      const response = await fetch(outlineEditor.dataset.outlineUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "fetch" },
        body: JSON.stringify({ outline: outlineEditor.value }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "outline save failed");
      renderOutlinePreview();
      if (payload.mapping) renderWritingSectionMap(payload.mapping);
      window.alert("大纲已保存。");
    } catch (error) {
      window.alert(error.message || "保存大纲失败。");
    }
  });

  document.querySelector("[data-save-writing-mappings]")?.addEventListener("click", async (event) => {
    try {
      const mappings = [];
      writingWorkbench.querySelectorAll("[data-map-paper-id]").forEach((row) => {
        const paperId = row.dataset.mapPaperId;
        const sectionId = row.dataset.mapSectionId;
        if (!paperId || !sectionId) return;
        const valueFor = (field) => row.querySelector(`[data-map-field="${field}"]`)?.value.trim() || "";
        mappings.push({
          section_id: sectionId,
          paper_id: paperId,
          citation_role: valueFor("citation_role"),
          writing_note: valueFor("writing_note"),
          evidence_detail: valueFor("evidence_detail"),
          missing_detail: valueFor("missing_detail"),
        });
      });
      const response = await fetch(event.currentTarget.dataset.mappingsUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "fetch" },
        body: JSON.stringify({ mappings }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "mapping save failed");
      if (payload.mapping) renderWritingSectionMap(payload.mapping);
      window.alert("内容核对已保存并同步小节-文献映射。");
    } catch (error) {
      window.alert(error.message || "保存内容核对失败。");
    }
  });

  sectionMap?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-section-mapping]");
    if (!button) return;
    button.closest("[data-map-paper-id]")?.remove();
  });

  const renderDraftPreview = () => {
    if (!draftPreview || !draftEditor) return;
    draftPreview.innerHTML = renderMarkdown(draftEditor.value || "");
    draftPreview.querySelectorAll("a").forEach((link) => {
      link.target = "_blank";
      link.rel = "noreferrer";
    });
  };

  document.querySelectorAll("[data-writing-draft-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      const preview = button.dataset.writingDraftMode === "preview";
      if (preview) renderDraftPreview();
      if (draftEditor) draftEditor.hidden = preview;
      if (draftPreview) draftPreview.hidden = !preview;
    });
  });

  document.querySelector("[data-save-writing-draft]")?.addEventListener("click", async () => {
    try {
      const response = await fetch(draftEditor.dataset.draftUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "fetch" },
        body: JSON.stringify({ markdown: draftEditor.value }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "draft save failed");
      window.alert("综述 Markdown 已保存。");
    } catch (error) {
      window.alert(error.message || "保存综述 Markdown 失败。");
    }
  });

  const hasWritingActions = (actions) => {
    if (!actions || typeof actions !== "object") return false;
    return ["topic_options", "search_prompts", "matrix_field_suggestions"].some((key) => Array.isArray(actions[key]) && actions[key].length);
  };

  const renderWritingActions = (actions) => {
    if (!hasWritingActions(actions)) return "";
    const topicItems = actions.topic_options || [];
    const topics = topicItems.length ? `
      <div class="writing-topic-choice-card">
        <div class="writing-topic-choice-head">
          <strong>选择综述主题</strong>
          <span>点击一个选项即可记录为当前主题</span>
        </div>
        <div class="writing-topic-choice-list">
          ${topicItems.map((item) => `
            <button type="button" class="writing-topic-option" data-select-writing-topic="${escapeAttribute(item.title || "")}">
              <span class="topic-option-id">${escapeHtml(item.id || "选")}</span>
              <span class="topic-option-main">
                <strong>${escapeHtml(item.title || "")}</strong>
                ${item.reason ? `<small>${escapeHtml(item.reason)}</small>` : ""}
              </span>
            </button>
          `).join("")}
        </div>
        <div class="writing-topic-custom">
          <input type="text" data-writing-custom-topic placeholder="或者输入你自己的主题">
          <button type="button" class="writing-action-btn" data-select-writing-topic-custom>采用其他主题</button>
        </div>
      </div>
    ` : "";
    const search = (actions.search_prompts || []).map((item) => `
      <button type="button" class="writing-action-btn primary" data-jump-writing-search="${escapeAttribute(item.request || "")}">
        跳转检索${item.label ? `：${escapeHtml(item.label)}` : ""}
      </button>
    `).join("");
    const matrixFields = actions.matrix_field_suggestions || [];
    const matrix = matrixFields.length ? `
      <button type="button" class="writing-action-btn matrix" data-jump-writing-matrix="${escapeAttribute(JSON.stringify(matrixFields))}">
        跳转文献矩阵：新增 ${matrixFields.length} 个字段
      </button>
    ` : "";
    const jumps = search || matrix ? `<div class="writing-jump-actions">${search}${matrix}</div>` : "";
    return `<div class="writing-action-panel">${jumps}${topics}</div>`;
  };

  const hydrateWritingActionPanels = () => {
    chatWindow?.querySelectorAll("[data-writing-actions]").forEach((panel) => {
      try {
        const actions = JSON.parse(panel.dataset.writingActions || "{}");
        panel.outerHTML = renderWritingActions(actions);
      } catch (_error) {
        panel.remove();
      }
    });
  };

  const renderWritingMessage = (message) => {
    if (message.role === "divider") return `<div class="chat-divider"><span>${escapeHtml(message.content || "新的对话")}</span></div>`;
    const isUser = message.role === "user";
    const content = isUser ? `<p>${escapeHtml(message.content || "")}</p>` : `<div class="markdown-body">${renderMarkdown(message.content || "")}</div>`;
    const actions = !isUser ? renderWritingActions(message.actions || {}) : "";
    return `
      <article class="chat-message ${isUser ? "user" : "assistant"}">
        <div class="chat-avatar">${isUser ? "我" : "光"}</div>
        <div class="chat-bubble">
          <div class="chat-meta">
            <span>${isUser ? "我的问题" : "综述写作"}</span>
            <time>${escapeHtml(message.created_at || "")}</time>
          </div>
          ${content}
          ${actions}
        </div>
      </article>
    `;
  };

  const renderWritingRunning = (task) => task ? `
    <article class="chat-message assistant running">
      <div class="chat-avatar">光</div>
      <div class="chat-bubble">
        <div class="chat-meta"><span>综述写作</span><time>${escapeHtml(task.started_at || "")}</time></div>
        <p>正在处理当前阶段任务。</p>
        ${task.total_sections ? `
          <div class="matrix-progress-head">
            <strong>${task.current_section ? `当前小节：${escapeHtml(task.current_section)}` : "正在生成小节-文献映射"}</strong>
            <span>${task.completed_sections || 0} / ${task.total_sections || 0}</span>
          </div>
          <div class="matrix-progress-bar"><span style="width:${Math.round(((task.completed_sections || 0) / (task.total_sections || 1)) * 100)}%"></span></div>
        ` : ""}
        <div class="chat-result-line loading-line"><span class="loading-dot"></span>写作中，请稍候</div>
        <div class="task-event-list">${Array.isArray(task.events) ? task.events.slice(-8).map((event) => `<div class="task-event-item">${escapeHtml(event.message || "")}</div>`).join("") : ""}</div>
      </div>
    </article>
  ` : "";

  const renderWritingChat = (messages, task = null) => {
    if (!chatWindow) return;
    if ((!messages || !messages.length) && !task) {
      chatWindow.innerHTML = '<div class="chat-empty"><strong>开始综述写作</strong><span>可以先从“帮我拟定一个合适的文献综述主题”开始。</span></div>';
      return;
    }
    chatWindow.innerHTML = [...(messages || []).map(renderWritingMessage), renderWritingRunning(task)].join("");
    hydrateWritingActionPanels();
    chatWindow.scrollTop = chatWindow.scrollHeight;
  };

  const setWritingBusy = (busy) => {
    if (chatSubmitButton) {
      chatSubmitButton.disabled = false;
      if (busy) {
        chatSubmitButton.type = "button";
        chatSubmitButton.classList.add("stop-send-btn");
        chatSubmitButton.setAttribute("data-stop-writing-chat", "");
        chatSubmitButton.innerHTML = '<span class="stop-icon"></span>';
      } else {
        chatSubmitButton.type = "submit";
        chatSubmitButton.classList.remove("stop-send-btn");
        chatSubmitButton.removeAttribute("data-stop-writing-chat");
        chatSubmitButton.textContent = "➤";
      }
    }
    if (chatTextarea) chatTextarea.readOnly = busy;
    if (compactButton) compactButton.disabled = busy;
  };

  const pollWritingStatus = async () => {
    const response = await fetch(chatForm.dataset.statusUrl, { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    renderWritingChat(payload.messages || [], payload.running ? payload.latest : null);
    if (!payload.running) {
      window.clearInterval(writingPollTimer);
      writingPollTimer = null;
      setWritingBusy(false);
      if (payload.outline && outlineEditor) outlineEditor.value = payload.outline;
      if (payload.draft && draftEditor) draftEditor.value = payload.draft;
      if (payload.mapping) renderWritingSectionMap(payload.mapping);
      renderOutlinePreview();
      renderDraftPreview();
    }
  };

  const startWritingPolling = () => {
    if (writingPollTimer) return;
    writingPollTimer = window.setInterval(pollWritingStatus, 3000);
  };

  chatForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = chatTextarea?.value.trim() || "";
    if (!question) {
      window.alert("请输入综述写作任务。");
      return;
    }
    setWritingBusy(true);
    try {
      const response = await fetch(chatForm.action, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "fetch" },
        body: JSON.stringify({ user_question: question, stage: currentStage }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "writing chat failed");
      if (chatTextarea) chatTextarea.value = "";
      renderWritingChat(payload.messages || [payload.user_message].filter(Boolean), {
        run_id: payload.run_id,
        status: "running",
        started_at: "",
      });
      startWritingPolling();
    } catch (error) {
      setWritingBusy(false);
      window.alert(error.message || "提交综述写作任务失败。");
    }
  });

  chatForm?.addEventListener("click", async (event) => {
    const stop = event.target.closest("[data-stop-writing-chat]");
    if (!stop) return;
    event.preventDefault();
    const response = await fetch(chatForm.dataset.stopUrl, { method: "POST", headers: { Accept: "application/json", "X-Requested-With": "fetch" } });
    const payload = await response.json();
    renderWritingChat(payload.messages || []);
    setWritingBusy(false);
    if (writingPollTimer) window.clearInterval(writingPollTimer);
    writingPollTimer = null;
  });

  chatWindow?.addEventListener("click", async (event) => {
    const searchButton = event.target.closest("[data-jump-writing-search]");
    if (searchButton) {
      const request = searchButton.dataset.jumpWritingSearch || "";
      if (writingProjectId && request) {
        window.localStorage.setItem(`guangming-search-prefill:${writingProjectId}`, JSON.stringify({ request, mode: "quick" }));
      }
      window.location.href = writingWorkbench.dataset.searchUrl || "/search";
      return;
    }
    const matrixButton = event.target.closest("[data-jump-writing-matrix]");
    if (matrixButton) {
      try {
        const fields = JSON.parse(matrixButton.dataset.jumpWritingMatrix || "[]");
        if (writingProjectId && fields.length) {
          window.localStorage.setItem(`guangming-matrix-field-drafts:${writingProjectId}`, JSON.stringify({ fields, source: "综述写作建议" }));
        }
      } catch (_error) {
        // Ignore malformed action payloads.
      }
      window.location.href = `${writingWorkbench.dataset.libraryUrl || "/library"}#matrix`;
      return;
    }
    const topicButton = event.target.closest("[data-select-writing-topic]");
    const customButton = event.target.closest("[data-select-writing-topic-custom]");
    if (!topicButton && !customButton) return;
    const customInput = customButton?.closest(".writing-topic-custom")?.querySelector("[data-writing-custom-topic]");
    const topic = customButton ? customInput?.value : topicButton.dataset.selectWritingTopic;
    if (!topic?.trim() || !writingTopicUrl) return;
    try {
      const response = await fetch(writingTopicUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "fetch" },
        body: JSON.stringify({ topic: topic.trim() }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "topic save failed");
      const topicDisplay = writingWorkbench.querySelector("[data-writing-topic-display]");
      if (topicDisplay) {
        topicDisplay.textContent = `已选主题：${payload.topic}`;
        topicDisplay.classList.remove("is-empty");
      }
      renderWritingChat(payload.messages || []);
    } catch (error) {
      window.alert(error.message || "保存综述主题失败。");
    }
  });

  compactButton?.addEventListener("click", async () => {
    if (!window.confirm("确定要压缩当前综述写作对话记忆吗？压缩完成后会继续沿用当前线程。")) return;
    const originalText = compactButton.textContent;
    compactButton.disabled = true;
    compactButton.textContent = "压缩中...";
    try {
      const response = await fetch(chatForm.dataset.compactUrl, {
        method: "POST",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "compact failed");
      if (writingPollTimer) window.clearInterval(writingPollTimer);
      writingPollTimer = null;
      setWritingBusy(false);
      renderWritingChat(payload.messages || []);
    } catch (error) {
      window.alert(error.message || "压缩综述写作对话记忆失败。");
    } finally {
      compactButton.disabled = false;
      compactButton.textContent = originalText || "压缩记忆";
    }
  });

  document.querySelector("[data-reset-writing-chat]")?.addEventListener("click", async () => {
    if (!window.confirm("确定要重置综述写作对话吗？这会开启新的对话线程，但保留历史分割线。")) return;
    const response = await fetch(chatForm.dataset.resetUrl, { method: "POST", headers: { Accept: "application/json", "X-Requested-With": "fetch" } });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      window.alert(payload.error || "重置综述写作对话失败。");
      return;
    }
    renderWritingChat(payload.messages || []);
  });

  quickRow?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-writing-quick]");
    if (!button) return;
    if (chatTextarea) chatTextarea.value = button.dataset.writingQuick || "";
    chatTextarea?.focus();
  });

  updateWritingStageHelpers();
  renderWritingSectionMap(writingMapping);
  setOutlineMode("preview");
  hydrateWritingActionPanels();

  const savedWidth = window.localStorage.getItem(splitStoreKey);
  if (savedWidth) writingWorkbench.style.setProperty("--writing-left-width", `${savedWidth}px`);
  writingWorkbench.querySelector("[data-writing-resizer]")?.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    const rect = writingWorkbench.getBoundingClientRect();
    writingWorkbench.classList.add("is-resizing");
    const onMove = (moveEvent) => {
      const width = Math.min(Math.max(360, moveEvent.clientX - rect.left), rect.width - 420);
      writingWorkbench.style.setProperty("--writing-left-width", `${Math.round(width)}px`);
      window.localStorage.setItem(splitStoreKey, String(Math.round(width)));
    };
    const onUp = () => {
      writingWorkbench.classList.remove("is-resizing");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });

  if (document.querySelector(".writing-chat-pane .chat-message.running")) {
    setWritingBusy(true);
    startWritingPolling();
  }
}

const pdfLookupButton = document.querySelector("[data-pdf-lookup]");
if (pdfLookupButton) {
  const pdfLookupStatusBox = document.querySelector("[data-pdf-lookup-status-box]");
  let pdfLookupPollTimer = null;

  const renderPdfLookupStatus = (task) => {
    if (!pdfLookupStatusBox) return;
    if (hiddenProgressKinds.has("pdf-lookup")) return;
    if (!task) {
      pdfLookupStatusBox.classList.remove("is-active");
      pdfLookupStatusBox.innerHTML = "";
      return;
    }
    pdfLookupStatusBox.classList.add("is-active");
    const total = Number(task.total || 0);
    const completed = Number(task.completed || 0);
    const failed = Number(task.failed || 0);
    const skipped = Number(task.skipped || 0);
    const percent = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
    const events = Array.isArray(task.events)
      ? task.events.slice(-6).map((event) => `<div class="task-event-item">${escapeHtml(event.message || "")}</div>`).join("")
      : "";
    pdfLookupStatusBox.innerHTML = `
      <div class="matrix-progress-head">
        <strong>${task.status === "running" ? "正在查找开放 PDF" : "PDF 查找任务已结束"}</strong>
        <span>${completed} / ${total}${failed ? `，未找到 ${failed}` : ""}${skipped ? `，跳过 ${skipped}` : ""}</span>
        <button class="progress-close-btn" type="button" data-dismiss-progress aria-label="隐藏进度">×</button>
      </div>
      <div class="matrix-progress-bar"><span style="width:${percent}%"></span></div>
      <div class="task-event-list" data-pdf-lookup-events>${events}</div>
    `;
  };

  const setPdfLookupBusy = (busy) => {
    pdfLookupButton.disabled = false;
    pdfLookupButton.textContent = busy ? "停止 PDF 查找" : "PDF 查找";
    pdfLookupButton.classList.toggle("danger", busy);
  };

  const pollPdfLookupStatus = async () => {
    if (!pdfLookupButton.dataset.pdfLookupStatusUrl) return;
    const response = await fetch(pdfLookupButton.dataset.pdfLookupStatusUrl, { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    renderPdfLookupStatus(payload.latest);
    updatePdfCells(payload.papers || []);
    if (!payload.running) {
      window.clearInterval(pdfLookupPollTimer);
      pdfLookupPollTimer = null;
      setPdfLookupBusy(false);
    }
  };

  const startPdfLookupPolling = () => {
    if (pdfLookupPollTimer) return;
    pdfLookupPollTimer = window.setInterval(pollPdfLookupStatus, 2500);
  };

  pdfLookupButton.addEventListener("click", async () => {
    if (pdfLookupButton.classList.contains("danger")) {
      const response = await fetch(pdfLookupButton.dataset.pdfLookupStopUrl, {
        method: "POST",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json();
      renderPdfLookupStatus(payload.latest);
      updatePdfCells(payload.papers || []);
      window.clearInterval(pdfLookupPollTimer);
      pdfLookupPollTimer = null;
      setPdfLookupBusy(false);
      return;
    }

    const paperIds = paperChecks.filter((item) => item.checked).map((item) => item.value);
    if (!paperIds.length) {
      window.alert("请先勾选需要查找 PDF 的文献。");
      return;
    }
    hiddenProgressKinds.delete("pdf-lookup");
    setPdfLookupBusy(true);
    try {
      const response = await fetch(pdfLookupButton.dataset.pdfLookupRunUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
        body: JSON.stringify({ paper_ids: paperIds }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "pdf lookup failed");
      renderPdfLookupStatus(payload.task);
      startPdfLookupPolling();
    } catch (error) {
      setPdfLookupBusy(false);
      window.alert(error.message || "启动 PDF 查找任务失败。");
    }
  });

  if (pdfLookupStatusBox?.classList.contains("is-active")) {
    setPdfLookupBusy(true);
    startPdfLookupPolling();
  }
}

const pdfDownloadButton = document.querySelector("[data-download-pdfs]");
if (pdfDownloadButton) {
  const pdfDownloadStatusBox = document.querySelector("[data-pdf-download-status-box]");
  let pdfDownloadPollTimer = null;

  const renderPdfDownloadStatus = (task) => {
    if (!pdfDownloadStatusBox) return;
    if (hiddenProgressKinds.has("pdf-download")) return;
    if (!task) {
      pdfDownloadStatusBox.classList.remove("is-active");
      pdfDownloadStatusBox.innerHTML = "";
      return;
    }
    pdfDownloadStatusBox.classList.add("is-active");
    const total = Number(task.total || 0);
    const completed = Number(task.completed || 0);
    const failed = Number(task.failed || 0);
    const skipped = Number(task.skipped || 0);
    const percent = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
    const events = Array.isArray(task.events)
      ? task.events.slice(-6).map((event) => `<div class="task-event-item">${escapeHtml(event.message || "")}</div>`).join("")
      : "";
    pdfDownloadStatusBox.innerHTML = `
      <div class="matrix-progress-head">
        <strong>${task.status === "running" ? "正在下载 PDF" : "PDF 下载任务已结束"}</strong>
        <span>${completed} / ${total}${failed ? `，失败 ${failed}` : ""}${skipped ? `，跳过 ${skipped}` : ""}</span>
        <button class="progress-close-btn" type="button" data-dismiss-progress aria-label="隐藏进度">×</button>
      </div>
      <div class="matrix-progress-bar"><span style="width:${percent}%"></span></div>
      <div class="task-event-list" data-pdf-download-events>${events}</div>
    `;
  };

  const setPdfDownloadBusy = (busy) => {
    pdfDownloadButton.disabled = false;
    pdfDownloadButton.textContent = busy ? "停止 PDF 下载" : "下载 PDF";
    pdfDownloadButton.classList.toggle("danger", busy);
  };

  const pollPdfDownloadStatus = async () => {
    if (!pdfDownloadButton.dataset.pdfDownloadStatusUrl) return;
    const response = await fetch(pdfDownloadButton.dataset.pdfDownloadStatusUrl, { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    renderPdfDownloadStatus(payload.latest);
    updatePdfCells(payload.papers || []);
    if (!payload.running) {
      window.clearInterval(pdfDownloadPollTimer);
      pdfDownloadPollTimer = null;
      setPdfDownloadBusy(false);
    }
  };

  const startPdfDownloadPolling = () => {
    if (pdfDownloadPollTimer) return;
    pdfDownloadPollTimer = window.setInterval(pollPdfDownloadStatus, 2500);
  };

  pdfDownloadButton.addEventListener("click", async (event) => {
    event.preventDefault();
    if (pdfDownloadButton.classList.contains("danger")) {
      const response = await fetch(pdfDownloadButton.dataset.pdfDownloadStopUrl, {
        method: "POST",
        headers: { Accept: "application/json", "X-Requested-With": "fetch" },
      });
      const payload = await response.json();
      renderPdfDownloadStatus(payload.latest);
      updatePdfCells(payload.papers || []);
      window.clearInterval(pdfDownloadPollTimer);
      pdfDownloadPollTimer = null;
      setPdfDownloadBusy(false);
      return;
    }

    const paperIds = paperChecks.filter((item) => item.checked).map((item) => item.value);
    if (!paperIds.length) {
      window.alert("请先勾选需要下载 PDF 的文献。");
      return;
    }
    hiddenProgressKinds.delete("pdf-download");
    setPdfDownloadBusy(true);
    try {
      const response = await fetch(pdfDownloadButton.dataset.pdfDownloadRunUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
        body: JSON.stringify({ paper_ids: paperIds }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "pdf download failed");
      renderPdfDownloadStatus(payload.task);
      startPdfDownloadPolling();
    } catch (error) {
      setPdfDownloadBusy(false);
      window.alert(error.message || "启动 PDF 下载任务失败。");
    }
  });

  if (pdfDownloadStatusBox?.classList.contains("is-active")) {
    setPdfDownloadBusy(true);
    startPdfDownloadPolling();
  }
}

document.querySelectorAll("[data-pdf-upload-button]").forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    button.closest(".pdf-upload-form")?.querySelector("[data-pdf-upload-input]")?.click();
  });
});

document.querySelectorAll("[data-pdf-upload-input]").forEach((input) => {
  input.addEventListener("change", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    const form = input.closest(".pdf-upload-form");
    if (!form || !input.files?.length) return;
    const button = form.querySelector("[data-pdf-upload-button]");
    const originalText = button?.textContent || "↑";
    if (button) {
      button.disabled = true;
      button.textContent = "...";
    }
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("upload failed");
      window.location.reload();
    } catch (_error) {
      window.alert("PDF 上传失败，请确认文件是 PDF。");
      if (button) {
        button.disabled = false;
        button.textContent = originalText;
      }
      input.value = "";
    }
  });
});

document.addEventListener("click", async (event) => {
  const tagButton = event.target.closest("[data-edit-tags]");
  if (tagButton) {
    event.preventDefault();
    event.stopPropagation();
    const tags = await openTagDropdown(tagButton);
    if (!tags) return;
    const payload = await updatePaperMeta(tagButton.dataset.updateUrl, { tags });
    if (payload?.ok) {
      tagButton.dataset.tags = tags.join(",");
      syncCustomTags(payload.project_tags || []);
      renderTagCell(tagButton.closest("[data-tag-cell]"), tags);
    }
    return;
  }

  const notesButton = event.target.closest("[data-edit-notes]");
  if (notesButton) {
    event.preventDefault();
    event.stopPropagation();
    const value = await openNotesEditor(notesButton.dataset.notes || "");
    if (value === null) return;
    const payload = await updatePaperMeta(notesButton.dataset.updateUrl, { notes: value });
    if (payload?.ok) {
      notesButton.dataset.notes = value;
      const cell = notesButton.closest(".notes-cell");
      const text = cell?.querySelector("[data-notes-text]");
      if (text) text.textContent = value || "无备注";
    }
  }
}, true);

function openTagDropdown(trigger) {
  const currentTags = (trigger.dataset.tags || "").split(",").map((item) => item.trim()).filter(Boolean);
  const builtinTags = ["重点", "方法类", "综述"];
  const savedCustomTags = readCustomTags(trigger);
  const currentCustomTags = currentTags.filter((tag) => !builtinTags.includes(tag));
  const customTags = uniqueTags([...savedCustomTags, ...currentCustomTags]);
  const allChoices = [...builtinTags, ...customTags];
  const body = document.createElement("div");
  body.className = "tag-dropdown";
  body.innerHTML = `
    <div class="tag-choice-list">
      ${allChoices
        .map(
          (tag) => `
            <label class="tag-choice ${builtinTags.includes(tag) ? "" : "custom-choice"}">
              <input type="checkbox" value="${escapeAttribute(tag)}" ${currentTags.includes(tag) ? "checked" : ""}>
              <span class="library-tag" style="${tagStyle(tag, customTags)}">${escapeHtml(tag)}</span>
              ${
                builtinTags.includes(tag)
                  ? ""
                  : `<button type="button" class="tag-delete-btn" data-delete-tag="${escapeAttribute(tag)}" title="删除标签">×</button>`
              }
            </label>
          `,
        )
        .join("")}
    </div>
    <label class="meta-editor-field">
      <span>自定义标签</span>
      <input type="text" value="${escapeAttribute(customTags.join("，"))}" placeholder="多个标签用逗号分隔">
    </label>
    <div class="tag-color-hint">
      ${allChoices
        .map((tag) => `<span class="library-tag" style="${tagStyle(tag, customTags)}">${escapeHtml(tag)}</span>`)
        .join("")}
    </div>
    <div class="tag-dropdown-actions">
      <button type="button" class="ghost-btn small" data-tag-cancel>取消</button>
      <button type="button" class="primary-btn small" data-tag-save>保存</button>
    </div>
  `;

  return new Promise((resolve) => {
    closeTagDropdown();
    document.body.appendChild(body);
    const rect = trigger.getBoundingClientRect();
    body.style.left = `${Math.min(rect.left, window.innerWidth - 340)}px`;
    body.style.top = `${rect.bottom + 8}px`;

    const finish = (value) => {
      body.remove();
      document.removeEventListener("click", outsideClick, true);
      resolve(value);
    };
    const collectTags = () => {
      const selected = Array.from(body.querySelectorAll("input[type='checkbox']:checked")).map((item) => item.value);
      const customInput = body.querySelector("input[type='text']");
      const custom = (customInput?.value || "")
        .split(/[,，]/)
        .map((item) => item.trim())
        .filter(Boolean);
      return Array.from(new Set([...selected, ...custom]));
    };
    const deleteCustomTag = async (tag) => {
      if (!tag || !trigger.dataset.deleteTagUrl) return;
      if (!window.confirm(`确认删除标签“${tag}”吗？已标注该标签的文献也会同步移除。`)) return;
      const response = await fetch(trigger.dataset.deleteTagUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ tag }),
      });
      if (!response.ok) return;
      const payload = await response.json();
      if (!payload.ok) return;
      syncCustomTags(payload.project_tags || []);
      removeTagFromRows(tag);
      finish(null);
    };
    const outsideClick = (event) => {
      if (!body.contains(event.target) && event.target !== trigger) finish(null);
    };
    body.querySelectorAll("[data-delete-tag]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        deleteCustomTag(button.dataset.deleteTag || "");
      });
    });
    body.querySelector("[data-tag-save]")?.addEventListener("click", () => finish(collectTags()));
    body.querySelector("[data-tag-cancel]")?.addEventListener("click", () => finish(null));
    window.setTimeout(() => document.addEventListener("click", outsideClick, true), 0);
    body.querySelector("input")?.focus();
  });
}

function closeTagDropdown() {
  document.querySelectorAll(".tag-dropdown").forEach((item) => item.remove());
}

function readCustomTags(trigger) {
  try {
    const parsed = JSON.parse(trigger.dataset.customTags || "[]");
    return Array.isArray(parsed) ? parsed.map((item) => String(item).trim()).filter(Boolean) : [];
  } catch (_error) {
    return [];
  }
}

function syncCustomTags(tags) {
  const cleaned = uniqueTags(tags);
  document.querySelectorAll("[data-edit-tags]").forEach((trigger) => {
    trigger.dataset.customTags = JSON.stringify(cleaned);
  });
}

function removeTagFromRows(tag) {
  document.querySelectorAll("[data-edit-tags]").forEach((trigger) => {
    const tags = (trigger.dataset.tags || "").split(",").map((item) => item.trim()).filter(Boolean);
    if (!tags.includes(tag)) return;
    const nextTags = tags.filter((item) => item !== tag);
    trigger.dataset.tags = nextTags.join(",");
    renderTagCell(trigger.closest("[data-tag-cell]"), nextTags);
  });
}

function uniqueTags(tags) {
  return Array.from(new Set((tags || []).map((item) => String(item).trim()).filter(Boolean)));
}

function openNotesEditor(currentValue) {
  const body = document.createElement("div");
  body.className = "meta-editor-form";
  body.innerHTML = `
    <label class="meta-editor-field">
      <span>简短备注</span>
      <textarea rows="5" placeholder="记录筛选理由、阅读提醒或和综述相关的备注">${escapeHtml(currentValue)}</textarea>
    </label>
  `;

  return openMetaEditor({
    title: "编辑文献备注",
    body,
    onConfirm: () => body.querySelector("textarea")?.value.trim() || "",
  });
}

function openMetaEditor({ title, body, onConfirm }) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "meta-editor-overlay";
    overlay.innerHTML = `
      <div class="meta-editor-card" role="dialog" aria-modal="true" aria-label="${escapeAttribute(title)}">
        <div class="meta-editor-head">
          <strong>${escapeHtml(title)}</strong>
          <button type="button" class="icon-action-btn" data-meta-cancel aria-label="关闭">×</button>
        </div>
        <div class="meta-editor-body"></div>
        <div class="meta-editor-actions">
          <button type="button" class="ghost-btn small" data-meta-cancel>取消</button>
          <button type="button" class="primary-btn small" data-meta-save>保存</button>
        </div>
      </div>
    `;
    overlay.querySelector(".meta-editor-body").appendChild(body);
    document.body.appendChild(overlay);

    const close = (value) => {
      overlay.remove();
      resolve(value);
    };
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay || event.target.closest("[data-meta-cancel]")) {
        close(null);
      }
      if (event.target.closest("[data-meta-save]")) {
        close(onConfirm());
      }
    });
    overlay.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close(null);
    });
    const firstInput = overlay.querySelector("input, textarea, button");
    firstInput?.focus();
  });
}

async function updatePaperMeta(url, payload) {
  if (!url) return null;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) return null;
  return response.json();
}

function renderTagCell(cell, tags) {
  const list = cell?.querySelector(".tag-list");
  if (!list) return;
  if (!tags.length) {
    list.innerHTML = '<span class="muted-inline">未标注</span>';
    return;
  }
  const customTags = readCustomTags(cell);
  list.innerHTML = tags.map((tag) => `<span class="library-tag" style="${tagStyle(tag, customTags)}">${escapeHtml(tag)}</span>`).join("");
}

function tagStyle(tag, customTags = []) {
  const palette = [
    ["#fff0e8", "#b95a28"],
    ["#eaf3ff", "#315fbe"],
    ["#eaf8ef", "#2f8b57"],
    ["#f2efff", "#6654c6"],
    ["#fff7db", "#9b6a10"],
    ["#e8f7f5", "#167a72"],
    ["#ffeaf1", "#b34368"],
    ["#edf0ff", "#4d5bbd"],
    ["#edf7df", "#5b7f1f"],
    ["#fff1d6", "#a45f16"],
    ["#e6f0f8", "#2d6c8f"],
    ["#f7edf4", "#96507c"],
  ];
  const builtinIndex = ["重点", "方法类", "综述"].indexOf(tag);
  const customIndex = uniqueTags(customTags).indexOf(tag);
  const index = builtinIndex >= 0 ? builtinIndex : customIndex >= 0 ? (3 + customIndex) % palette.length : hashTag(tag) % palette.length;
  return `--tag-bg:${palette[index][0]};--tag-color:${palette[index][1]};`;
}

function hashTag(tag) {
  return Array.from(String(tag)).reduce((sum, char, index) => sum + char.charCodeAt(0) * (index + 1), 0);
}

const legacyModelSettingsRoot = null;
if (legacyModelSettingsRoot) {
  const profilesUrl = modelSettingsRoot.dataset.profilesUrl;
  const testUrl = modelSettingsRoot.dataset.testUrl;
  const bridgeStatusUrl = modelSettingsRoot.dataset.bridgeStatusUrl;
  const profileList = modelSettingsRoot.querySelector("[data-model-profile-list]");
  const form = modelSettingsRoot.querySelector("[data-model-profile-form]");
  const titleNode = modelSettingsRoot.querySelector("[data-model-editor-title]");
  const bridgeBadge = modelSettingsRoot.querySelector("[data-model-bridge-badge]");
  const resultBox = modelSettingsRoot.querySelector("[data-model-test-result]");
  const bridgeStatusBox = modelSettingsRoot.querySelector("[data-bridge-status-box]");
  const modeSelect = form?.querySelector("[data-model-mode-select]");
  let profilesCache = [];
  let activeProfileId = "";

  const profileModeLabel = (mode) => (mode === "chat_via_bridge" ? "使用本地路由" : "原生 Responses");

  const blankProfile = () => ({
    id: "",
    name: "",
    note: "",
    api_key: "",
    base_url: "https://api.openai.com",
    model: "",
    mode: "responses_native",
    reasoning_effort_default: "high",
    bridge_capabilities: {
      thinking_toggle_supported: true,
      thinking_default_enabled: true,
      reasoning_level_mapping_supported: false,
      reasoning_level_mapping_enabled: false,
      upstream_protocol: "openai_chat",
    },
  });

  const readFormPayload = () => ({
    name: form.querySelector("[name='name']")?.value.trim() || "",
    note: form.querySelector("[name='note']")?.value.trim() || "",
    api_key: form.querySelector("[name='api_key']")?.value.trim() || "",
    base_url: form.querySelector("[name='base_url']")?.value.trim() || "",
    model: form.querySelector("[name='model']")?.value.trim() || "",
    mode: modeSelect?.value || "responses_native",
    reasoning_effort_default: form.querySelector("[name='reasoning_effort_default']")?.value || "high",
    bridge_capabilities: {
      thinking_toggle_supported: !!form.querySelector("[name='bridge_capabilities.thinking_toggle_supported']")?.checked,
      thinking_default_enabled: !!form.querySelector("[name='bridge_capabilities.thinking_default_enabled']")?.checked,
      reasoning_level_mapping_supported: !!form.querySelector("[name='bridge_capabilities.reasoning_level_mapping_supported']")?.checked,
      reasoning_level_mapping_enabled: !!form.querySelector("[name='bridge_capabilities.reasoning_level_mapping_enabled']")?.checked,
      upstream_protocol: "openai_chat",
    },
  });

  const setResult = (kind, message) => {
    if (!resultBox) return;
    resultBox.hidden = false;
    resultBox.className = `settings-test-result ${kind}`;
    resultBox.textContent = message;
  };

  const clearResult = () => {
    if (!resultBox) return;
    resultBox.hidden = true;
    resultBox.className = "settings-test-result";
    resultBox.textContent = "";
  };

  const renderModePanels = () => {
    const mode = modeSelect?.value || "responses_native";
    form.querySelectorAll("[data-mode-panel]").forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.modePanel !== mode);
    });
    if (bridgeBadge) bridgeBadge.textContent = profileModeLabel(mode);
    if (bridgeStatusBox && mode !== "chat_via_bridge") {
      bridgeStatusBox.textContent = "当前模型使用原生 Responses。";
    }
  };

  const fillForm = (profile) => {
    const target = {
      ...blankProfile(),
      ...profile,
      bridge_capabilities: {
        ...blankProfile().bridge_capabilities,
        ...(profile.bridge_capabilities || {}),
      },
    };
    form.dataset.editingId = target.id || "";
    if (titleNode) titleNode.textContent = target.id ? "编辑模型" : "新增模型";
    form.querySelector("[name='name']").value = target.name || "";
    form.querySelector("[name='note']").value = target.note || "";
    form.querySelector("[name='api_key']").value = target.api_key || "";
    form.querySelector("[name='base_url']").value = target.base_url || "";
    form.querySelector("[name='model']").value = target.model || "";
    modeSelect.value = target.mode || "responses_native";
    form.querySelector("[name='reasoning_effort_default']").value = target.reasoning_effort_default || "high";
    form.querySelector("[name='bridge_capabilities.thinking_toggle_supported']").checked = !!target.bridge_capabilities.thinking_toggle_supported;
    form.querySelector("[name='bridge_capabilities.thinking_default_enabled']").checked = !!target.bridge_capabilities.thinking_default_enabled;
    form.querySelector("[name='bridge_capabilities.reasoning_level_mapping_supported']").checked = !!target.bridge_capabilities.reasoning_level_mapping_supported;
    form.querySelector("[name='bridge_capabilities.reasoning_level_mapping_enabled']").checked = !!target.bridge_capabilities.reasoning_level_mapping_enabled;
    clearResult();
    renderModePanels();
  };

  const renderProfiles = () => {
    if (!profileList) return;
    profileList.innerHTML = profilesCache.map((profile) => `
      <article class="model-profile-card ${profile.id === activeProfileId ? "active" : ""}" data-model-profile-card data-profile='${escapeAttribute(JSON.stringify(profile))}'>
        <div class="model-profile-head">
          <div>
            <strong>${escapeHtml(profile.name || "未命名模型")}</strong>
            <span>${escapeHtml(profileModeLabel(profile.mode))}</span>
          </div>
          ${profile.id === activeProfileId ? '<span class="badge">使用中</span>' : ""}
        </div>
        <div class="model-profile-meta">
          <span>${escapeHtml(profile.base_url || "")}</span>
          <span>${escapeHtml(profile.model || "")}</span>
        </div>
        <div class="model-profile-actions">
          ${profile.id === activeProfileId ? "" : `<button class="ghost-btn small" type="button" data-model-profile-activate="${escapeAttribute(profile.id)}">启用</button>`}
          <button class="ghost-btn small" type="button" data-model-profile-edit="${escapeAttribute(profile.id)}">编辑</button>
          <button class="ghost-btn small danger" type="button" data-model-profile-delete="${escapeAttribute(profile.id)}">删除</button>
        </div>
      </article>
    `).join("");
  };

  const loadProfiles = async () => {
    const response = await fetch(profilesUrl, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "加载模型配置失败。");
    profilesCache = payload.profiles || [];
    activeProfileId = payload.active_profile_id || "";
    renderProfiles();
    const current = profilesCache.find((item) => item.id === form.dataset.editingId)
      || profilesCache.find((item) => item.id === activeProfileId)
      || blankProfile();
    fillForm(current);
  };

  const refreshBridgeStatus = async () => {
    const payload = readFormPayload();
    if (!bridgeStatusBox) return;
    if (payload.mode !== "chat_via_bridge") {
      bridgeStatusBox.textContent = "当前模型使用原生 Responses。";
      return;
    }
    try {
      const response = await fetch(`${bridgeStatusUrl}?mode=chat_via_bridge`, { cache: "no-store" });
      const data = await response.json();
      bridgeStatusBox.textContent = data.status?.message || "本地路由状态未知。";
    } catch (_error) {
      bridgeStatusBox.textContent = "无法获取本地路由状态。";
    }
  };

  modelSettingsRoot.addEventListener("click", async (event) => {
    const editButton = event.target.closest("[data-model-profile-edit]");
    if (editButton) {
      const profile = profilesCache.find((item) => item.id === editButton.dataset.modelProfileEdit);
      if (profile) fillForm(profile);
      return;
    }

    const activateButton = event.target.closest("[data-model-profile-activate]");
    if (activateButton) {
      const response = await fetch(`${profilesUrl}/${encodeURIComponent(activateButton.dataset.modelProfileActivate)}/activate`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        window.alert(payload.error || "启用模型失败。");
        return;
      }
      await loadProfiles();
      return;
    }

    const deleteButton = event.target.closest("[data-model-profile-delete]");
    if (deleteButton) {
      if (!window.confirm("确认删除这条模型配置吗？")) return;
      const response = await fetch(`${profilesUrl}/${encodeURIComponent(deleteButton.dataset.modelProfileDelete)}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        window.alert(payload.error || "删除模型失败。");
        return;
      }
      await loadProfiles();
      return;
    }

    if (event.target.closest("[data-model-profile-new]")) {
      fillForm(blankProfile());
    }

    if (event.target.closest("[data-model-profile-test]")) {
      clearResult();
      setResult("pending", "正在执行真实连通测试，请稍候...");
      const response = await fetch(testUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(readFormPayload()),
      });
      const payload = await response.json();
      setResult(payload.ok ? "success" : "error", `${payload.path || "测试"}：${payload.message || "测试失败"}`);
    }
  });

  modeSelect?.addEventListener("change", async () => {
    renderModePanels();
    clearResult();
    await refreshBridgeStatus();
  });

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const editingId = form.dataset.editingId || "";
    const payload = readFormPayload();
    const response = await fetch(
      editingId ? `${profilesUrl}/${encodeURIComponent(editingId)}` : profilesUrl,
      {
        method: editingId ? "PATCH" : "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
      },
    );
    const result = await response.json();
    if (!response.ok || !result.ok) {
      setResult("error", result.error || "保存失败。");
      return;
    }
    setResult("success", "模型配置已保存。");
    await loadProfiles();
  });

  loadProfiles().catch((error) => {
    setResult("error", error.message || "模型配置加载失败。");
  });
}
