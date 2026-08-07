let ws = null;
let reconnectTimeout = null;
let heartbeatInterval = null;

const CAPTCHA_WORKER_COUNT = 5;
const CAPTCHA_WORKER_URL = "https://labs.google/fx/tools/flow";
const CAPTCHA_WORKER_TAB_IDS_KEY = "captchaWorkerTabIds";
const CAPTCHA_WORKER_RECOVERY_COOLDOWN_MS = 60000;
const captchaWorkers = Array.from({ length: CAPTCHA_WORKER_COUNT }, (_, index) => ({
    index,
    tabId: null,
    queue: Promise.resolve(),
    lastRecoveryAt: 0
}));
let nextCaptchaWorkerIndex = 0;
let workerPoolInitialized = false;
let workerPoolInitPromise = null;

const DEFAULT_SETTINGS = {
    serverUrl: "ws://127.0.0.1:8001/captcha_ws",
    apiKey: "",
    routeKey: "",
    clientLabel: ""
};

function getSettings() {
    return new Promise((resolve) => {
        chrome.storage.local.get(DEFAULT_SETTINGS, (stored) => {
            resolve({
                serverUrl: (stored.serverUrl || DEFAULT_SETTINGS.serverUrl).trim(),
                apiKey: (stored.apiKey || "").trim(),
                routeKey: (stored.routeKey || "").trim(),
                clientLabel: (stored.clientLabel || "").trim()
            });
        });
    });
}

function closeSocket() {
    if (heartbeatInterval) clearInterval(heartbeatInterval);
    heartbeatInterval = null;
    if (reconnectTimeout) clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
    if (ws) {
        try {
            ws.close();
        } catch (e) {
            console.log("[Flow2API] Close socket error", e);
        }
        ws = null;
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function getStoredWorkerTabIds() {
    return new Promise((resolve) => {
        chrome.storage.local.get({ [CAPTCHA_WORKER_TAB_IDS_KEY]: [] }, (stored) => {
            resolve(Array.isArray(stored[CAPTCHA_WORKER_TAB_IDS_KEY])
                ? stored[CAPTCHA_WORKER_TAB_IDS_KEY]
                : []);
        });
    });
}

function persistWorkerTabIds() {
    const tabIds = captchaWorkers.map(worker => worker.tabId);
    return new Promise((resolve) => {
        chrome.storage.local.set({ [CAPTCHA_WORKER_TAB_IDS_KEY]: tabIds }, resolve);
    });
}

function getTab(tabId) {
    return new Promise((resolve, reject) => {
        chrome.tabs.get(tabId, (tab) => {
            if (chrome.runtime.lastError || !tab) {
                reject(new Error(chrome.runtime.lastError?.message || "Worker tab not found"));
                return;
            }
            resolve(tab);
        });
    });
}

async function initializeWorkerPool() {
    if (workerPoolInitialized) return;
    if (workerPoolInitPromise) return workerPoolInitPromise;

    workerPoolInitPromise = (async () => {
        const storedTabIds = await getStoredWorkerTabIds();
        await Promise.all(captchaWorkers.map(async (worker) => {
            const storedTabId = Number(storedTabIds[worker.index]);
            if (!Number.isInteger(storedTabId)) return;
            try {
                const tab = await getTab(storedTabId);
                if ((tab.url || "").startsWith("https://labs.google/")) {
                    worker.tabId = storedTabId;
                }
            } catch (e) {
                worker.tabId = null;
            }
        }));
        workerPoolInitialized = true;
        await persistWorkerTabIds();
    })().finally(() => {
        workerPoolInitPromise = null;
    });

    return workerPoolInitPromise;
}

async function ensureWorkerTab(worker) {
    await initializeWorkerPool();

    if (worker.tabId) {
        try {
            const tab = await getTab(worker.tabId);
            if ((tab.url || "").startsWith("https://labs.google/")) {
                if (tab.status !== "complete") await waitForTabReady(worker.tabId);
                return worker.tabId;
            }
        } catch (e) {
            worker.tabId = null;
        }
    }

    console.log(`[Flow2API] Creating persistent captcha worker ${worker.index + 1}/${CAPTCHA_WORKER_COUNT}...`);
    const tab = await chrome.tabs.create({
        url: CAPTCHA_WORKER_URL,
        active: false,
        pinned: true
    });
    worker.tabId = tab.id;
    await persistWorkerTabIds();

    try {
        await chrome.tabs.update(worker.tabId, { autoDiscardable: false });
    } catch (e) {
        console.log("[Flow2API] Could not disable worker tab discarding:", e);
    }

    await waitForTabReady(worker.tabId);
    await sleep(1200);
    return worker.tabId;
}

async function recoverWorkerTab(worker) {
    const now = Date.now();
    if (now - worker.lastRecoveryAt < CAPTCHA_WORKER_RECOVERY_COOLDOWN_MS) {
        throw new Error("Worker recovery is cooling down");
    }
    worker.lastRecoveryAt = now;

    const tabId = await ensureWorkerTab(worker);
    console.warn(`[Flow2API] Reloading captcha worker ${worker.index + 1} after a failed solve.`);
    await chrome.tabs.reload(tabId);
    await waitForTabReady(tabId);
    await sleep(1200);
    return tabId;
}

function waitForTabReady(tabId, timeoutMs = 12000) {
    return new Promise((resolve) => {
        let settled = false;
        const finish = () => {
            if (settled) return;
            settled = true;
            chrome.tabs.onUpdated.removeListener(onUpdated);
            clearTimeout(timer);
            resolve();
        };
        const onUpdated = (updatedTabId, changeInfo) => {
            if (updatedTabId === tabId && changeInfo.status === "complete") {
                finish();
            }
        };
        const timer = setTimeout(finish, timeoutMs);

        chrome.tabs.onUpdated.addListener(onUpdated);
        chrome.tabs.get(tabId, (tab) => {
            if (chrome.runtime.lastError) {
                finish();
                return;
            }
            if (tab && tab.status === "complete") {
                finish();
            }
        });
    });
}

async function connectWS() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

    const settings = await getSettings();
    const url = new URL(settings.serverUrl || DEFAULT_SETTINGS.serverUrl);
    if (settings.apiKey) {
        url.searchParams.set("key", settings.apiKey);
    }
    if (settings.routeKey) {
        url.searchParams.set("route_key", settings.routeKey);
    }
    if (settings.clientLabel) {
        url.searchParams.set("client_label", settings.clientLabel);
    }

    ws = new WebSocket(url.toString());

    ws.onopen = () => {
        console.log("[Flow2API] Background connected to WebSocket", url.toString());
        ws.send(JSON.stringify({
            type: "register",
            route_key: settings.routeKey,
            client_label: settings.clientLabel
        }));
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        heartbeatInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "ping" }));
            }
        }, 20000);
    };

    ws.onmessage = async (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            return;
        }

        if (data.type === "register_ack") {
            console.log("[Flow2API] Registered route key:", data.route_key || "(empty)");
            return;
        }

        if (data.type === "get_token") {
            const worker = captchaWorkers[nextCaptchaWorkerIndex];
            nextCaptchaWorkerIndex = (nextCaptchaWorkerIndex + 1) % CAPTCHA_WORKER_COUNT;
            worker.queue = worker.queue
                .then(() => handleGetToken(data, worker))
                .catch(err => console.error(`[Flow2API] Worker ${worker.index + 1} queue error:`, err));
        }
    };

    ws.onclose = () => {
        console.log("[Flow2API] WebSocket Closed. Reconnecting in 2s...");
        ws = null;
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        if (reconnectTimeout) clearTimeout(reconnectTimeout);
        reconnectTimeout = setTimeout(connectWS, 2000);
    };

    ws.onerror = (e) => {
        console.log("[Flow2API] WebSocket Error", e);
    };
}

async function executeCaptchaInWorker(tabId, action, scriptTimeoutMs) {
    const results = await chrome.scripting.executeScript({
                target: { tabId },
                world: "MAIN",
                func: async (action, timeoutMs) => {
                    return new Promise((resolve, reject) => {
                        let settled = false;
                        const finish = (fn, value) => {
                            if (settled) return;
                            settled = true;
                            fn(value);
                        };
                        try {
                            function run() {
                                grecaptcha.enterprise.ready(function() {
                                    grecaptcha.enterprise.execute("6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV", { action: action })
                                        .then(token => finish(resolve, token))
                                        .catch(err => finish(reject, err.message || "reCAPTCHA evaluation failed internally"));
                                });
                            }

                            if (typeof grecaptcha !== "undefined" && grecaptcha.enterprise) {
                                run();
                            } else {
                                const s = document.createElement("script");
                                s.src = "https://www.google.com/recaptcha/enterprise.js?render=6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV";
                                s.onload = run;
                                s.onerror = () => finish(reject, "Failed to load enterprise.js via network");
                                document.head.appendChild(s);
                            }

                            setTimeout(() => finish(reject, "Timeout generating reCAPTCHA locally"), timeoutMs);
                        } catch (e) {
                            finish(reject, e.message);
                        }
                    });
                },
                args: [action, scriptTimeoutMs]
            });

    if (results && results[0] && results[0].result) return results[0].result;
    throw new Error("No token returned from worker tab");
}

function sendCaptchaResponse(payload) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        throw new Error("Captcha WebSocket is not connected");
    }
    ws.send(JSON.stringify(payload));
}

async function handleGetToken(data, worker) {
    const action = data.action || "IMAGE_GENERATION";
    const scriptTimeoutMs = action === "VIDEO_GENERATION" ? 30000 : 20000;
    let lastError = null;

    try {
        let tabId = await ensureWorkerTab(worker);
        let captchaToken;

        try {
            captchaToken = await executeCaptchaInWorker(tabId, action, scriptTimeoutMs);
        } catch (firstError) {
            lastError = firstError;
            tabId = await recoverWorkerTab(worker);
            captchaToken = await executeCaptchaInWorker(tabId, action, scriptTimeoutMs);
        }

        sendCaptchaResponse({
                req_id: data.req_id,
                status: "success",
                token: captchaToken
            });
    } catch (err) {
        lastError = err || lastError;
        try {
            sendCaptchaResponse({
                req_id: data.req_id,
                status: "error",
                error: "Extension worker failed: " + (lastError?.message || "Unknown error")
            });
        } catch (sendError) {
            console.error("[Flow2API] Could not send captcha error response:", sendError);
        }
    }
}

chrome.tabs.onRemoved.addListener((tabId) => {
    const worker = captchaWorkers.find(item => item.tabId === tabId);
    if (!worker) return;
    worker.tabId = null;
    persistWorkerTabIds();
    console.log(`[Flow2API] Captcha worker ${worker.index + 1} was closed; it will be recreated on demand.`);
});

chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.routeKey || changes.serverUrl || changes.apiKey || changes.clientLabel) {
        console.log("[Flow2API] Extension settings changed, reconnecting WebSocket...");
        closeSocket();
        connectWS();
    }
});

connectWS();
