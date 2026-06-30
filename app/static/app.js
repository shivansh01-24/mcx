// State variables
let socket = null;
let currentTab = 'curl';
let tickerChart = null;

// Chart history storage
const chartHistory = {
    gold: [],
    silver: [],
    labels: []
};

// Start initialization
document.addEventListener("DOMContentLoaded", () => {
    // 1. Initialize Chart.js
    initChart();
    
    // 2. Initial system status fetch
    fetchSystemStatus();
    setInterval(fetchSystemStatus, 5000);

    // 3. Connect real-time WebSocket
    connectWebSocket();

    // 4. Update code snippet view
    updateCodeSnippet();

    // 5. Setup event listeners
    document.getElementById("key-generator-form").addEventListener("submit", generateApiKey);
    document.getElementById("dev-api-key").addEventListener("input", () => {
        updateCodeSnippet();
    });
});

function connectWebSocket() {
    const apiKey = document.getElementById("dev-api-key").value || "mcx_pub_dev_key";
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/live?api_key=${apiKey}`;

    console.log(`Connecting to WebSocket: ${wsUrl}`);
    addLogLine("info", `Connecting to WebSocket: ${wsUrl}`);

    if (socket) {
        socket.close();
    }

    try {
        socket = new WebSocket(wsUrl);
    } catch (e) {
        addLogLine("error", `Failed to instantiate WebSocket: ${e.message}`);
        setTimeout(connectWebSocket, 5000);
        return;
    }

    socket.onopen = () => {
        console.log("WebSocket connected.");
        addLogLine("info", "Real-Time WebSocket subscription activated.");
        document.getElementById("header-status-dot").className = "status-indicator-light glowing";
        document.getElementById("header-status-text").textContent = "CONNECTED (LIVE)";
    };

    socket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            
            // Check message type
            if (data.type === "heartbeat") {
                document.getElementById("ws-client-count").textContent = data.clients_connected;
                return;
            }

            if (data.commodity) {
                updatePriceUI(data.commodity, data);
                updateChartData(data.commodity, data.price);
                
                // Print structured log
                const logMsg = `[TICK] ${data.commodity.toUpperCase()} Price updated to ${data.price} from source '${data.collector}' (Confidence: ${data.confidence}%)`;
                addLogLine("info", `{"timestamp":"${data.timestamp}", "level":"INFO", "logger":"Consensus", "message":"${logMsg}"}`);
            }
        } catch (e) {
            console.error("Error processing websocket message:", e);
        }
    };

    socket.onclose = (event) => {
        console.warn("WebSocket connection closed. Attempting reconnect...");
        addLogLine("warn", "WebSocket disconnected. Reconnecting in 5 seconds...");
        document.getElementById("header-status-dot").className = "status-indicator-light";
        document.getElementById("header-status-text").textContent = "DISCONNECTED";
        setTimeout(connectWebSocket, 5000);
    };

    socket.onerror = (error) => {
        console.error("WebSocket error:", error);
    };
}

function updatePriceUI(commodity, data) {
    const isGold = commodity === "gold";
    const priceElem = document.getElementById(isGold ? "gold-price" : "silver-price");
    const changeElem = document.getElementById(isGold ? "gold-change" : "silver-change");
    const pctElem = document.getElementById(isGold ? "gold-change-pct" : "silver-change-pct");
    const ohlcElem = document.getElementById(isGold ? "gold-ohlc" : "silver-ohlc");
    const confElem = document.getElementById(isGold ? "gold-confidence" : "silver-confidence");
    const cardElem = document.getElementById(isGold ? "gold-price-card" : "silver-price-card");

    // Track old price to flash updates
    const oldPrice = parseFloat(priceElem.textContent.replace(/,/g, ""));
    const newPrice = data.price;
    
    priceElem.textContent = newPrice.toLocaleString("en-IN", { minimumFractionDigits: 2 });
    
    // Set changes
    const change = data.change || 0.0;
    const changePct = data.change_percent || 0.0;
    const formattedChange = (change >= 0 ? "+" : "") + change.toFixed(2);
    const formattedPct = `(${(changePct >= 0 ? "+" : "")}${changePct.toFixed(2)}%)`;

    changeElem.textContent = formattedChange;
    pctElem.textContent = formattedPct;
    
    if (change >= 0) {
        changeElem.className = "price-change-value positive";
        pctElem.className = "price-change-pct positive";
    } else {
        changeElem.className = "price-change-value negative";
        pctElem.className = "price-change-pct negative";
    }

    // Min Max bounds
    const low = data.low ? data.low.toLocaleString("en-IN") : "--";
    const high = data.high ? data.high.toLocaleString("en-IN") : "--";
    ohlcElem.textContent = `₹${low} / ₹${high}`;

    // Confidence
    confElem.textContent = `${data.confidence}%`;
    if (data.stale) {
        confElem.textContent = "STALE DATA";
        confElem.style.color = "var(--red-neon)";
    } else if (data.estimated) {
        confElem.textContent = `${data.confidence}% (EST)`;
        confElem.style.color = "var(--cyan-neon)";
    } else {
        confElem.style.color = "var(--text-primary)";
    }

    // Flash animation on card depending on tick trend direction
    if (!isNaN(oldPrice) && oldPrice !== newPrice) {
        const trend = newPrice > oldPrice ? "glowing-up" : "glowing-down";
        cardElem.classList.add(trend);
        setTimeout(() => cardElem.classList.remove(trend), 800);
    }
}

// Stats periodically fetched via REST
async function fetchSystemStatus() {
    const apiKey = document.getElementById("dev-api-key").value || "mcx_pub_dev_key";
    try {
        const res = await fetch(`/api/v1/status?api_key=${apiKey}`);
        if (!res.ok) return;
        const body = await res.json();
        
        // Unwrap REST envelope
        const data = body.data;
        if (!data) return;

        // Render Telemetry
        document.getElementById("redis-status").textContent = data.database;
        document.getElementById("redis-status").className = `badge ${data.redis === "CONNECTED" ? "badge-success" : "badge-danger"}`;
        document.getElementById("postgres-status").textContent = data.database;
        document.getElementById("postgres-status").className = `badge ${data.database === "CONNECTED" ? "badge-success" : "badge-danger"}`;
        
        document.getElementById("cpu-usage").textContent = `${data.cpu_usage_percent.toFixed(1)}%`;
        document.getElementById("ram-usage").textContent = `${data.memory_usage_mb.toFixed(0)} MB`;
        document.getElementById("disk-usage").textContent = `${data.disk_usage_percent.toFixed(1)}%`;
        
        document.getElementById("active-source-count").textContent = Object.values(data.active_source).filter(v => v !== "None").length;
        document.getElementById("primary-collector-name").textContent = data.active_source.gold || "None";

        // Render Leaderboard
        const tableBody = document.querySelector("#leaderboard-table tbody");
        tableBody.innerHTML = "";
        data.collectors.forEach(c => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${c.collector_name}</strong> (v${c.version})</td>
                <td><span class="badge">${c.collector_type}</span></td>
                <td>${c.avg_latency_ms} ms</td>
                <td>${c.success_rate}%</td>
                <td><span class="badge ${c.circuit_breaker_status === "CLOSED" ? "badge-success" : "badge-danger"}">${c.circuit_breaker_status}</span></td>
                <td><strong>${c.health_score}/100</strong></td>
            `;
            tableBody.appendChild(tr);
        });

        // Trigger rank changes status broadcast on console if primary source changes
        const currentActive = data.active_source.gold;
        const activeLabel = document.getElementById("primary-collector-name");
        if (activeLabel.textContent !== currentActive && currentActive !== "None") {
            addLogLine("warn", `{"timestamp":"${new Date().toISOString()}", "level":"WARN", "logger":"CollectorManager", "message":"Active source switched to '${currentActive}' based on rank scores"}`);
        }

    } catch (e) {
        console.error("Error fetching status:", e);
    }
}

// API Key Administration
async function generateApiKey(e) {
    e.preventDefault();
    const adminKey = document.getElementById("dev-api-key").value;
    const owner = document.getElementById("owner-name").value;
    const plan = document.getElementById("key-plan").value;
    const desc = document.getElementById("key-description").value;

    try {
        const res = await fetch(`/api/v1/admin/keys?owner=${encodeURIComponent(owner)}&plan=${plan}&description=${encodeURIComponent(desc)}&api_key=${adminKey}`, {
            method: "POST"
        });
        const body = await res.json();
        
        if (body.success) {
            const keyData = body.data;
            addLogLine("info", `{"timestamp":"${new Date().toISOString()}", "level":"INFO", "logger":"Auth", "message":"Generated new ${plan} API Key for ${owner}"}`);
            
            // Append to table
            const tableBody = document.querySelector("#keys-table tbody");
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><code>${keyData.key_value}</code></td>
                <td>${keyData.owner}</td>
                <td><span class="badge badge-premium">${keyData.plan}</span></td>
                <td>Just Now</td>
                <td><button class="btn-revoke" onclick="revokeKey('${keyData.key_value}')">Revoke</button></td>
            `;
            tableBody.appendChild(tr);
            
            // Reset form
            document.getElementById("owner-name").value = "";
            document.getElementById("key-description").value = "";
        } else {
            alert(`Error: ${body.error}`);
        }
    } catch (err) {
        alert(`Failed: ${err.message}`);
    }
}

async function revokeKey(keyValue) {
    const adminKey = document.getElementById("dev-api-key").value;
    if (!confirm(`Are you sure you want to revoke key: ${keyValue}?`)) return;

    try {
        const res = await fetch(`/api/v1/admin/keys/${keyValue}?api_key=${adminKey}`, {
            method: "DELETE"
        });
        const body = await res.json();
        if (body.success) {
            addLogLine("warn", `{"timestamp":"${new Date().toISOString()}", "level":"WARN", "logger":"Auth", "message":"Revoked API Key '${keyValue}'"}`);
            alert("Key revoked successfully.");
            // Refresh table or page would be ideal, but for now we manually delete the row
            location.reload();
        } else {
            alert(`Error: ${body.error}`);
        }
    } catch (err) {
        alert(err.message);
    }
}

// Chart.js Setup
function initChart() {
    const ctx = document.getElementById('tickerChart').getContext('2d');
    
    // Initialize chart with empty labels and datasets
    tickerChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: chartHistory.labels,
            datasets: [
                {
                    label: 'Gold (10g)',
                    data: chartHistory.gold,
                    borderColor: '#f59e0b',
                    backgroundColor: 'rgba(245, 158, 11, 0.05)',
                    borderWidth: 2,
                    pointRadius: 1,
                    tension: 0.3,
                    fill: true,
                    yAxisID: 'y'
                },
                {
                    label: 'Silver (1kg)',
                    data: chartHistory.silver,
                    borderColor: '#a1a1aa',
                    backgroundColor: 'rgba(161, 161, 170, 0.05)',
                    borderWidth: 2,
                    pointRadius: 1,
                    tension: 0.3,
                    fill: true,
                    yAxisID: 'y1'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    labels: { color: '#9ca3af', font: { size: 10 } }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: '#6b7280', font: { size: 9 } }
                },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    grid: { color: 'rgba(255,255,255,0.03)' },
                    ticks: { color: '#f59e0b', font: { size: 9 } }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    ticks: { color: '#a1a1aa', font: { size: 9 } }
                }
            }
        }
    });
}

function updateChartData(commodity, price) {
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    
    // Check if we already have this timestamp (so we don't duplicate x-axis points)
    let labelIdx = chartHistory.labels.indexOf(now);
    if (labelIdx === -1) {
        chartHistory.labels.push(now);
        labelIdx = chartHistory.labels.length - 1;
        
        // Push previous price for the opposite commodity to maintain length
        if (commodity === "gold") {
            chartHistory.gold.push(price);
            const lastSilver = chartHistory.silver[chartHistory.silver.length - 1] || price * 1.5; // proxy start
            chartHistory.silver.push(lastSilver);
        } else {
            chartHistory.silver.push(price);
            const lastGold = chartHistory.gold[chartHistory.gold.length - 1] || price / 1.5; // proxy start
            chartHistory.gold.push(lastGold);
        }
    } else {
        // Update price for active timestamp
        if (commodity === "gold") {
            chartHistory.gold[labelIdx] = price;
        } else {
            chartHistory.silver[labelIdx] = price;
        }
    }

    // Keep history length limited to 15 ticks
    if (chartHistory.labels.length > 15) {
        chartHistory.labels.shift();
        chartHistory.gold.shift();
        chartHistory.silver.shift();
    }

    tickerChart.update("none"); // Update chart silently without resetting animations
}

// Developer Hub & Code Snippet Templates
function switchTab(tabName) {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    event.currentTarget.classList.add("active");
    currentTab = tabName;
    updateCodeSnippet();
}

function updateCodeSnippet() {
    const key = document.getElementById("dev-api-key").value || "mcx_pub_dev_key";
    const host = window.location.origin;
    const snippets = {
        curl: `curl -X GET "${host}/api/v1/prices?api_key=${key}" \\\n     -H "Accept: application/json"`,
        python: `import requests\n\nurl = "${host}/api/v1/prices"\nparams = {"api_key": "${key}"}\n\nresponse = requests.get(url, params=params)\nprint(response.json())`,
        node: `const axios = require('axios');\n\naxios.get('${host}/api/v1/prices', {\n  params: { api_key: '${key}' }\n})\n.then(res => console.log(res.data))\n.catch(err => console.error(err));`,
        go: `package main\n\nimport (\n\t"fmt"\n\t"io"\n\t"net/http"\n)\n\nfunc main() {\n\turl := "${host}/api/v1/prices?api_key=${key}"\n\tres, _ := http.Get(url)\n\tdefer res.Body.Close()\n\tbody, _ := io.ReadAll(res.Body)\n\tfmt.Println(string(body))\n}`,
        php: `<?php\n\n$url = "${host}/api/v1/prices?api_key=${key}";\n$response = file_get_contents($url);\necho $response;`,
        java: `import java.net.URI;\nimport java.net.http.HttpClient;\nimport java.net.http.HttpRequest;\nimport java.net.http.HttpResponse;\n\npublic class App {\n    public static void main(String[] args) throws Exception {\n        var client = HttpClient.newHttpClient();\n        var req = HttpRequest.newBuilder()\n            .uri(URI.create("${host}/api/v1/prices?api_key=${key}"))\n            .GET()\n            .build();\n        var res = client.send(req, HttpResponse.BodyHandlers.ofString());\n        System.out.println(res.body());\n    }\n}`
    };

    document.getElementById("snippet-code").textContent = snippets[currentTab];
}

// Log Terminal updates
function addLogLine(level, message) {
    const logsConsole = document.getElementById("logs-console-area");
    if (!logsConsole) return;

    const div = document.createElement("div");
    div.className = `log-line ${level}`;
    div.textContent = message;
    
    logsConsole.appendChild(div);
    
    // limit logs in dashboard console to 50 lines to prevent tab sluggishness
    while (logsConsole.children.length > 50) {
        logsConsole.removeChild(logsConsole.firstChild);
    }
    
    // Scroll to bottom
    logsConsole.scrollTop = logsConsole.scrollHeight;
}
