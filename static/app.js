(() => {
  const $ = (id) => document.getElementById(id);

  const RANDOM_PLACES = [
    "廃墟と化した闘技場", "深い霧に包まれた森", "崩れかけた古城の中庭",
    "満天の星空の下、砂漠のオアシス", "波打ち際の岩場", "誰もいない廃校の屋上",
    "雲の上に浮かぶ神殿", "灯りの消えた地下水路", "桜が舞う古い橋の上", "凍てついた氷の洞窟",
  ];
  const RANDOM_TIMES = [
    "夜明け前", "真昼", "夕暮れ", "真夜中", "嵐の最中", "雨上がりの直後", "満月の夜",
  ];
  const RANDOM_SITUATIONS = [
    "観衆が遠巻きに見守っている", "誰も見ていない静寂の中", "遠くで鐘の音が鳴り響いている",
    "強風が吹き荒れている", "地面がわずかに揺れている", "不気味な鳥の声が響いている",
    "祭りの喧騒がかすかに聞こえる", "何者かの視線を感じる",
  ];
  const pickRandom = (arr) => arr[Math.floor(Math.random() * arr.length)];

  function getPlayerId() {
    let id = localStorage.getItem("player_id");
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem("player_id", id);
    }
    return id;
  }

  function todayLocalDateStr() {
    const d = new Date();
    return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
  }

  const playerId = getPlayerId();
  let ws = null;
  let lastLogLength = 0;
  let actionSubmittedThisRound = false;
  let selectedCategory = null;
  let pendingGachaPassive = null;

  const screenHome = $("screen-home");
  const screenBattle = $("screen-battle");

  function showScreen(name) {
    screenHome.classList.toggle("hidden", name !== "home");
    screenBattle.classList.toggle("hidden", name !== "battle");
  }

  function showPassive(data) {
    $("passive-loading").classList.add("hidden");
    $("passive-custom-form").classList.add("hidden");
    $("gacha-panel").classList.add("hidden");
    $("gacha-animation").classList.add("hidden");
    $("passive-card").classList.remove("hidden");
    $("passive-name").textContent = data.name;
    $("passive-desc").textContent = data.desc;
  }

  async function loadPassive() {
    try {
      const res = await fetch(`/api/passive?player_id=${encodeURIComponent(playerId)}`);
      const data = await res.json();
      $("passive-loading").classList.add("hidden");
      if (data.pool_exhausted) {
        $("passive-custom-form").classList.remove("hidden");
        return;
      }
      if (localStorage.getItem("gacha_seen_date") === todayLocalDateStr()) {
        showPassive(data);
      } else {
        pendingGachaPassive = data;
        $("gacha-panel").classList.remove("hidden");
      }
    } catch (e) {
      $("passive-loading").textContent = "パッシブの取得に失敗しました。サーバーを確認してください。";
    }
  }

  $("btn-gacha").addEventListener("click", () => {
    $("gacha-panel").classList.add("hidden");
    $("gacha-animation").classList.remove("hidden");
    setTimeout(() => {
      $("gacha-animation").classList.add("hidden");
      showPassive(pendingGachaPassive);
      localStorage.setItem("gacha_seen_date", todayLocalDateStr());
    }, 1100);
  });

  $("btn-submit-custom").addEventListener("click", async () => {
    const text = $("input-custom-passive").value.trim();
    if (!text) return;
    const res = await fetch("/api/passive/custom", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player_id: playerId, text }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      $("custom-status").textContent =
        err.error === "already_assigned" ? "既にパッシブが確定しています。" : "登録できませんでした。";
      return;
    }
    const data = await res.json();
    showPassive(data);
    localStorage.setItem("gacha_seen_date", todayLocalDateStr());
  });

  function wsUrl(roomCode) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/ws/${roomCode}?player_id=${encodeURIComponent(playerId)}`;
  }

  function connectRoom(roomCode) {
    showScreen("battle");
    lastLogLength = 0;
    actionSubmittedThisRound = false;
    selectedCategory = null;
    $("log").innerHTML = "";
    $("game-over-panel").classList.add("hidden");
    $("action-panel").classList.remove("hidden");
    $("input-action-text").value = "";
    $("action-status").textContent = "";
    document.querySelectorAll(".category-btn").forEach((b) => b.classList.remove("selected"));
    $("room-code-value").textContent = roomCode;
    $("room-code-banner").classList.remove("hidden");

    ws = new WebSocket(wsUrl(roomCode));

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "error") {
        alert(msg.message);
        showScreen("home");
        ws.close();
        return;
      }
      if (msg.type === "state") {
        renderState(msg);
      }
    };
  }

  function renderState(msg) {
    $("stage-banner").textContent = msg.stage ? `舞台設定: ${msg.stage}` : "";

    $("hp-you-fill").style.width = `${Math.max(0, (msg.hp_you / msg.max_hp) * 100)}%`;
    $("hp-opp-fill").style.width = `${Math.max(0, (msg.hp_opponent / msg.max_hp) * 100)}%`;
    $("hp-you-text").textContent = `${Math.max(0, msg.hp_you)} / ${msg.max_hp}`;
    $("hp-opp-text").textContent = `${Math.max(0, msg.hp_opponent)} / ${msg.max_hp}`;

    if (msg.passive_you) {
      $("passive-you-name").textContent = msg.passive_you.name;
      $("passive-you-desc").textContent = msg.passive_you.desc;
    }
    if (msg.passive_opponent) {
      $("passive-opp-name").textContent = msg.passive_opponent.name;
      $("passive-opp-desc").textContent = msg.passive_opponent.desc;
    } else {
      $("passive-opp-name").textContent = "???";
      $("passive-opp-desc").textContent = "決着後に公開されます";
    }

    $("waiting-banner").classList.toggle("hidden", !msg.waiting_for_opponent);

    if (msg.log.length !== lastLogLength) {
      $("log").innerHTML = msg.log
        .map((line) => `<div class="log-entry">${escapeHtml(line)}</div>`)
        .join("");
      $("log").scrollTop = $("log").scrollHeight;
      lastLogLength = msg.log.length;
      actionSubmittedThisRound = false;
    }

    const disableAction = msg.waiting_for_opponent || msg.game_over || actionSubmittedThisRound;
    $("input-action-text").disabled = disableAction;
    $("btn-send-action").disabled = disableAction;
    document.querySelectorAll(".category-btn").forEach((b) => (b.disabled = disableAction));
    $("action-status").textContent =
      actionSubmittedThisRound && !msg.game_over ? "相手の行動を待っています..." : "";

    if (msg.game_over) {
      $("action-panel").classList.add("hidden");
      $("game-over-panel").classList.remove("hidden");
      let text;
      if (msg.winner === "draw") {
        text = "引き分け！";
      } else if (msg.winner === msg.your_role) {
        text = "あなたの勝利！";
      } else {
        text = "あなたの敗北...";
      }
      $("game-over-text").textContent = text;
    }
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  $("btn-random-stage").addEventListener("click", () => {
    $("input-place").value = pickRandom(RANDOM_PLACES);
    $("input-time").value = pickRandom(RANDOM_TIMES);
    $("input-situation").value = pickRandom(RANDOM_SITUATIONS);
  });

  $("btn-create-room").addEventListener("click", async () => {
    const maxHp = parseInt($("input-max-hp").value, 10) || 100;
    const stageParts = [
      $("input-place").value.trim() && `場所: ${$("input-place").value.trim()}`,
      $("input-time").value.trim() && `時間: ${$("input-time").value.trim()}`,
      $("input-situation").value.trim() && `状況: ${$("input-situation").value.trim()}`,
    ].filter(Boolean);
    const stage = stageParts.join(" / ");

    const res = await fetch("/api/rooms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player_id: playerId, max_hp: maxHp, stage }),
    });
    const data = await res.json();
    connectRoom(data.room_code);
  });

  $("btn-join-room").addEventListener("click", () => {
    const code = $("input-room-code").value.trim().toUpperCase();
    if (!code) return;
    connectRoom(code);
  });

  document.querySelectorAll(".category-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedCategory = btn.dataset.category;
      document.querySelectorAll(".category-btn").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
    });
  });

  $("btn-send-action").addEventListener("click", () => {
    const text = $("input-action-text").value.trim();
    if (!text || !selectedCategory || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "action", category: selectedCategory, text }));
    actionSubmittedThisRound = true;
    $("input-action-text").value = "";
    $("input-action-text").disabled = true;
    $("btn-send-action").disabled = true;
    document.querySelectorAll(".category-btn").forEach((b) => (b.disabled = true));
    $("action-status").textContent = "相手の行動を待っています...";
  });

  $("input-action-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("btn-send-action").click();
  });

  $("btn-back-home").addEventListener("click", () => {
    if (ws) ws.close();
    showScreen("home");
    loadPoints();
  });

  async function loadPoints() {
    const res = await fetch(`/api/points?player_id=${encodeURIComponent(playerId)}`);
    const data = await res.json();
    $("points-value").textContent = data.points;
  }

  $("btn-submit-passive").addEventListener("click", async () => {
    const text = $("input-submit-passive").value.trim();
    if (!text) return;
    const res = await fetch("/api/passive/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player_id: playerId, text }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      $("submit-passive-status").textContent =
        err.error === "insufficient_points" ? "ポイントが足りません（10pt必要です）。" : "申請できませんでした。";
      return;
    }
    const data = await res.json();
    $("points-value").textContent = data.points;
    $("input-submit-passive").value = "";
    $("submit-passive-status").textContent = "申請しました。管理者の採用をお待ちください。";
  });

  loadPassive();
  loadPoints();
})();
