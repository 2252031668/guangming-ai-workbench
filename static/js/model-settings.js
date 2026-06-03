(function () {
  const root = document.querySelector("[data-model-settings]");
  if (!root) return;

  const profilesUrl = root.dataset.profilesUrl;
  const testUrl = root.dataset.testUrl;
  const renderChatCodeUrl = root.dataset.renderChatCodeUrl;
  const bridgeStatusUrl = root.dataset.bridgeStatusUrl;
  const profileList = root.querySelector("[data-model-profile-list]");
  const form = root.querySelector("[data-model-profile-form]");
  const titleNode = root.querySelector("[data-model-editor-title]");
  const badge = root.querySelector("[data-model-bridge-badge]");
  const resultBox = root.querySelector("[data-model-test-result]");
  const bridgeStatusBox = root.querySelector("[data-bridge-status-box]");
  const modeSelect = form?.querySelector("[data-model-mode-select]");
  const codeEditor = form?.querySelector("[data-chat-code-editor]");
  const codeHint = form?.querySelector("[data-chat-code-hint]");
  const codeState = form?.querySelector("[data-chat-code-state]");
  let profiles = [];
  let activeProfileId = "";
  let codeMode = "generated";
  let suppressDirty = false;

  const html = (value) => String(value || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);

  const attr = (value) => html(value).replaceAll("`", "&#96;");
  const modeLabel = (mode) => (mode === "chat_via_bridge" ? "使用本地路由" : "原生 Responses");

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
      reasoning_level_mapping_supported: true,
      reasoning_level_mapping_enabled: true,
      upstream_protocol: "openai_chat",
    },
    chat_request_config: {
      thinking_enabled: true,
      reasoning_level: "high",
      extra_body_template: JSON.stringify({ thinking: { type: "enabled" } }, null, 2),
    },
    context_window: "",
    max_output_tokens: "",
    generated_chat_code: "",
  });

  const normalizeProfile = (profile = {}) => ({
    ...blankProfile(),
    ...profile,
    bridge_capabilities: {
      ...blankProfile().bridge_capabilities,
      ...(profile.bridge_capabilities || {}),
    },
    chat_request_config: {
      ...blankProfile().chat_request_config,
      ...(profile.chat_request_config || {}),
    },
  });

  const readForm = () => {
    const thinkingEnabled = !!form.querySelector("[name='chat_request_config.thinking_enabled']")?.checked;
    const reasoningLevel = form.querySelector("[name='chat_request_config.reasoning_level']")?.value || "none";
    return {
      name: form.querySelector("[name='name']")?.value.trim() || "",
      note: form.querySelector("[name='note']")?.value.trim() || "",
      api_key: form.querySelector("[name='api_key']")?.value.trim() || "",
      base_url: form.querySelector("[name='base_url']")?.value.trim() || "",
      model: form.querySelector("[name='model']")?.value.trim() || "",
      mode: modeSelect?.value || "responses_native",
      reasoning_effort_default: form.querySelector("[name='reasoning_effort_default']")?.value || "high",
      context_window: form.querySelector("[name='context_window']")?.value.trim() || "",
      max_output_tokens: form.querySelector("[name='max_output_tokens']")?.value.trim() || "",
      bridge_capabilities: {
        thinking_toggle_supported: true,
        thinking_default_enabled: thinkingEnabled,
        reasoning_level_mapping_supported: true,
        reasoning_level_mapping_enabled: reasoningLevel !== "none",
        upstream_protocol: "openai_chat",
      },
      chat_request_config: {
        thinking_enabled: thinkingEnabled,
        reasoning_level: reasoningLevel,
      },
    };
  };

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

  const formatDiagnostics = (diagnostics = {}) => {
    const parts = [];
    if (diagnostics.turn_id) parts.push(`turn_id=${diagnostics.turn_id}`);
    if (diagnostics.status) parts.push(`status=${diagnostics.status}`);
    if (typeof diagnostics.agent_delta_chars === "number") parts.push(`delta_chars=${diagnostics.agent_delta_chars}`);
    if (typeof diagnostics.item_count === "number") parts.push(`items=${diagnostics.item_count}`);
    if (diagnostics.error) parts.push(`error=${diagnostics.error}`);
    return parts.join("，");
  };

  const formatTestResult = (result) => {
    const source = result.source ? `，来源：${result.source}` : "";
    const lines = [`${result.path || "测试"}${source}：${result.message || "测试失败"}`];
    if (result.assistant_text) {
      lines.push(`模型实际返回：${result.assistant_text}`);
    }
    const diag = formatDiagnostics(result.diagnostics || {});
    if (diag) {
      lines.push(`诊断：${diag}`);
    }
    return lines.join("\n");
  };

  const setCodeState = (nextMode) => {
    codeMode = nextMode;
    if (codeState) codeState.textContent = nextMode === "custom" ? "代码已自定义" : "表单生成";
    if (codeHint) {
      codeHint.textContent = nextMode === "custom"
        ? "当前测试将基于代码编辑区内容；如需覆盖，请点击“从表单重新生成”。"
        : "代码未自定义时会跟随表单自动刷新；你可以修改 reasoning_effort 或 extra_body 来适配不同供应商。";
    }
  };

  const writeCode = (code, nextMode = "generated") => {
    if (!codeEditor) return;
    suppressDirty = true;
    codeEditor.value = code || "";
    suppressDirty = false;
    setCodeState(nextMode);
  };

  const renderModePanels = () => {
    const mode = modeSelect?.value || "responses_native";
    form.querySelectorAll("[data-mode-panel]").forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.modePanel !== mode);
    });
    if (badge) badge.textContent = modeLabel(mode);
    if (bridgeStatusBox && mode !== "chat_via_bridge") {
      bridgeStatusBox.textContent = "原生 Responses";
    }
  };

  const renderBridgeStatus = (status = {}) => {
    if (bridgeStatusBox) {
      bridgeStatusBox.textContent = status.message || "本地路由状态未知。";
    }
  };

  const refreshBridgeStatus = async () => {
    const payload = readForm();
    if (!bridgeStatusBox) return;
    if (payload.mode !== "chat_via_bridge") {
      bridgeStatusBox.textContent = "原生 Responses";
      return;
    }
    try {
      const response = await fetch(`${bridgeStatusUrl}?mode=chat_via_bridge`, { cache: "no-store" });
      const data = await response.json();
      renderBridgeStatus(data.status || {});
    } catch (_error) {
      bridgeStatusBox.textContent = "无法获取本地路由状态。";
    }
  };

  const regenerateCode = async ({ force = false } = {}) => {
    if (!codeEditor || !renderChatCodeUrl) return;
    if (!force && codeMode === "custom") return;
    try {
      const response = await fetch(renderChatCodeUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(readForm()),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "无法生成请求代码");
      writeCode(payload.code || "", "generated");
    } catch (error) {
      setResult("error", error.message || "无法生成请求代码");
    }
  };

  const fillForm = (profile) => {
    const target = normalizeProfile(profile);
    form.dataset.editingId = target.id || "";
    if (titleNode) titleNode.textContent = target.id ? "编辑模型" : "新增模型";
    form.querySelector("[name='name']").value = target.name || "";
    form.querySelector("[name='note']").value = target.note || "";
    form.querySelector("[name='api_key']").value = target.api_key || "";
    form.querySelector("[name='base_url']").value = target.base_url || "";
    form.querySelector("[name='model']").value = target.model || "";
    form.querySelector("[name='context_window']").value = target.context_window ? String(target.context_window) : "";
    form.querySelector("[name='max_output_tokens']").value = target.max_output_tokens ? String(target.max_output_tokens) : "";
    modeSelect.value = target.mode || "responses_native";
    form.querySelector("[name='reasoning_effort_default']").value = target.reasoning_effort_default || "high";
    form.querySelector("[name='chat_request_config.thinking_enabled']").checked = !!target.chat_request_config.thinking_enabled;
    form.querySelector("[name='chat_request_config.reasoning_level']").value = target.chat_request_config.reasoning_level || "none";
    writeCode(target.generated_chat_code || "", "generated");
    clearResult();
    renderModePanels();
    refreshBridgeStatus();
  };

  const renderProfiles = () => {
    if (!profileList) return;
    profileList.innerHTML = profiles.map((profile) => `
      <article class="model-profile-card ${profile.id === activeProfileId ? "active" : ""}" data-model-profile-card>
        <div class="model-profile-head">
          <div>
            <strong>${html(profile.name || "未命名模型")}</strong>
            <span>${html(modeLabel(profile.mode))}</span>
          </div>
          ${profile.id === activeProfileId ? '<span class="badge">使用中</span>' : ""}
        </div>
        <div class="model-profile-meta">
          <span>Base URL：${html(profile.base_url || "")}</span>
          <span>模型名：${html(profile.model || "")}</span>
        </div>
        <div class="model-profile-actions">
          ${profile.id === activeProfileId ? "" : `<button class="ghost-btn small" type="button" data-model-profile-activate="${attr(profile.id)}">启用</button>`}
          <button class="ghost-btn small" type="button" data-model-profile-edit="${attr(profile.id)}">编辑</button>
          <button class="ghost-btn small danger" type="button" data-model-profile-delete="${attr(profile.id)}">删除</button>
        </div>
      </article>
    `).join("");
  };

  const loadProfiles = async () => {
    const response = await fetch(profilesUrl, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "加载模型配置失败。");
    profiles = (payload.profiles || []).map(normalizeProfile);
    activeProfileId = payload.active_profile_id || "";
    renderProfiles();
    const current = profiles.find((item) => item.id === form.dataset.editingId)
      || profiles.find((item) => item.id === activeProfileId)
      || blankProfile();
    fillForm(current);
  };

  const runTest = async (scope = "full") => {
    clearResult();
    const pendingText = {
      upstream_chat: "正在测试路由前上游请求，请稍候...",
      bridge_responses: "正在测试 Moon Bridge Responses 转换，请稍候...",
      codex_full: "正在测试完整 Codex 链路，请稍候...",
    }[scope] || "正在执行测试，请稍候...";
    setResult("pending", pendingText);
    const payload = readForm();
    payload.test_scope = scope;
    if (payload.mode === "chat_via_bridge") {
      payload.code_override = codeEditor?.value || "";
      payload.code_mode = codeMode;
    }
    const response = await fetch(testUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    setResult(result.ok ? "success" : "error", formatTestResult(result));
    if (result.bridge_status?.message) {
      renderBridgeStatus(result.bridge_status);
    }
  };

  root.addEventListener("click", async (event) => {
    const editButton = event.target.closest("[data-model-profile-edit]");
    if (editButton) {
      const profile = profiles.find((item) => item.id === editButton.dataset.modelProfileEdit);
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
      await regenerateCode({ force: true });
      return;
    }

    if (event.target.closest("[data-regenerate-chat-code]")) {
      await regenerateCode({ force: true });
      return;
    }

    if (event.target.closest("[data-run-chat-code-test]")) {
      await runTest("upstream_chat");
      return;
    }

    if (event.target.closest("[data-bridge-responses-test]")) {
      await runTest("bridge_responses");
      return;
    }

    if (event.target.closest("[data-model-profile-test]")) {
      await runTest("codex_full");
    }
  });

  codeEditor?.addEventListener("input", () => {
    if (!suppressDirty) setCodeState("custom");
  });

  form?.addEventListener("input", async (event) => {
    if (event.target?.matches("[data-chat-code-editor]")) return;
    clearResult();
    if (modeSelect?.value === "chat_via_bridge") {
      await regenerateCode();
    }
  });

  modeSelect?.addEventListener("change", async () => {
    renderModePanels();
    clearResult();
    await refreshBridgeStatus();
    await regenerateCode();
  });

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const editingId = form.dataset.editingId || "";
    const payload = readForm();
    if (payload.mode === "chat_via_bridge") {
      payload.code_override = codeEditor?.value || "";
      payload.code_mode = codeMode;
    }
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
})();
