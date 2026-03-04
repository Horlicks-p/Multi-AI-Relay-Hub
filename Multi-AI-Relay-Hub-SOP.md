# Multi-AI Relay Hub 協作 SOP（v0.3）

> 適用範圍：本專案 `Multi-AI-Relay-Hub` 的多 AI 接力協作（討論、執行、審查、回報）。
>
> 目標：在維持協作效率的同時，降低「誤寫檔、亂碼、格式壞檔、重複操作、訊息噪音」風險，並讓 SOP 與實際程式行為可對齊。

## 1. 角色與責任

每一輪固定採用 `1 Executor + 2 Reviewers`：

- `Executor`：唯一可改檔 AI，負責實作、驗證、提交回報。
- `Reviewers`：只讀檢查，不改檔；負責技術核對、風險提示、驗證結果交叉確認。
- `Human`：定案需求、仲裁分歧、批准是否進入下一輪。

規則：

- 同一輪只允許 `Executor` 寫入檔案。
- `Reviewers` 若發現異常，只回報，不自行修檔（除非 Human 明確改派）。
- 發生異常時立即啟動「凍結機制」（見第 8 節）。

## 2. 核心原則（Single Source of Truth）

- `timeout`、`context 上限`、`encoding`、`timezone` 皆應集中管理（優先 `.env` / 單一設定來源）。
- 禁止多檔硬編碼相同參數，避免規格漂移（Python wrapper / JS wrapper / relay 主程式不一致）。
- SOP 為流程基準；若程式尚未硬性實作，需在 SOP 明示「目前為流程約束，非系統強制」。

目前建議集中管理的環境變數：

- `RELAY_TIMEOUT_SEC`：整體超時秒數（建議預設 `600`，可依任務類型下調到 `300`）。
- `MAX_CONTEXT_CHARS`：上下文字元上限（建議預設 `32000`，可調整為 `24000-32000`）。

`.env` 載入現況：

- 若程式未實作自動載入 `.env`（例如 `python-dotenv`），則需先以 shell `set/export` 將變數注入環境，`os.environ` 才能讀取。

## 3. 每輪標準流程

1. `Human` 發出本輪任務與角色指派。
2. `Executor` 先回覆執行計畫摘要（目標檔案、預計修改點、驗證方式）。
3. `Executor` 實作修改（小步驟，避免一次改太多）。
4. `Executor` 執行強制驗證（見第 5 節）。
5. `Executor` 使用固定模板回報（見第 7 節）。
6. `Reviewers` 依回報與實際檔案交叉檢查。
7. `Human` 判斷是否接受，或下達修正回合。

附註（架構現況與延後審核制）：

- 目前 Hub 採並行回覆，Reviewer 在同一輪看到的是**修改前**的版本。
- **正式規範**：Reviewer 採「延後一輪審核制」——在下一輪開始時，優先回覆對上一輪 Executor 產出的審核意見，再回應新議題。
- 此設計符合並行架構的技術現實，不犧牲回應速度。

## 4. 變更策略（Small and Safe）

- 一次只做一個主題（例如先修 timeout，再修 context 策略）。
- 每步都需可獨立驗證、可獨立回退。
- 優先最小變更，不做無關重構。
- 任何順手修都需先告知 Human。

## 5. 強制驗證清單（完成前必跑）

依檔案類型至少包含：

- Python 檔：`python -m py_compile <file>`（或等效語法檢查）
- JavaScript 檔：`node --check <file>`（或等效語法檢查）
- PHP 檔：`php -l <file>`
- JSON 檔：格式檢查（parse/lint）且確認無 trailing comma
- 關鍵邏輯：核對目標條件或函式位置是否正確生效
- 編碼一致性：確認 UTF-8（必要時無 BOM），避免寫檔流程引入亂碼

UTF-8 建議（PowerShell）：

```powershell
[console]::InputEncoding = [console]::OutputEncoding = New-Object System.Text.UTF8Encoding
```

## 6. Timeout / Context / 時間戳規範

### 6.1 Timeout 分級

- 純對話輪：建議 `180-300s`（`120s` 實測不足，不建議使用）
- 工具任務輪（讀檔/改檔/驗證）：建議 `300-600s`
- 重任務輪：可提高到 `600s`

執行原則：

- 目前最低共識：`120s` 不足，不建議作為任何任務的預設值。
- 預設可採 `300s`，複雜任務提升到 `600s`。
- timeout 需可由環境變數調整，不一次寫死。

### 6.2 Context 保留策略

- `MAX_CONTEXT_CHARS` 建議範圍：`24,000-32,000`。
- 禁止單純字串硬切到訊息中段。
- 優先策略：保留最近完整輪次（round-based retention）。

### 6.3 時間戳與時區

- 每輪摘要建議輸出 `ISO 8601`（含 UTC offset）時間戳。
- log 與回報需能看出事件順序與本地時區。

## 7. 固定回報模板（Executor）

每輪完成後固定使用以下格式：

```text
[修改檔案]
- path/to/fileA
- path/to/fileB

[Diff 摘要]
- 變更 1
- 變更 2

[驗證指令]
- command 1
- command 2

[驗證結果]
- Python syntax: PASS/FAIL
- JS syntax: PASS/FAIL
- PHP syntax: PASS/FAIL
- JSON parse: PASS/FAIL（附錯誤訊息）
- 關鍵條件命中: PASS/FAIL
- Encoding UTF-8: PASS/FAIL

[風險與待辦]
- 風險 1
- 建議下一步 1
```

## 8. 異常處理（凍結機制）

觸發條件（任一符合即觸發）：

- 檔案亂碼或編碼異常
- Python/JS/PHP/JSON 驗證失敗
- timeout 或工具回應異常
- Reviewer 指出可能破壞性變更

凍結流程：

1. 立刻停止所有寫入動作。
2. 回報目前狀態與最後一次成功步驟。
3. Human 指派單一 AI 修復。
4. 修復後重跑第 5 節驗證，再恢復流程。

治理責任：

- 觸發 freeze 後，僅 `Human` 可解凍。
- 連續 timeout 次數門檻（例如 2 次）可作為預設 freeze 條件，實際值由設定檔管理。

## 9. 寫入安全機制（含現況聲明）

寫檔前至少完成以下其中一種：

1. 先輸出預計 diff 摘要給 Human 確認。
2. 先建立可回復點（例如 git checkpoint）。

禁止事項：

- 未經指示不可覆蓋大型檔案全文（尤其 JSON / 設定檔）。
- 未驗證即宣告完成。

現況聲明：

- 自 v0.3 起，`RELAY_MODE=readonly`（預設）會在系統層移除各 CLI 的自動批准旗標（`bypassPermissions` / `--yolo` / `--dangerously-bypass-approvals-and-sandbox`）。
- 需要寫入時須手動切換 `RELAY_MODE=full`，或在 `.env` 中設定。
- 即使在 `full` 模式，仍需嚴格執行「先回報計畫，再做非唯讀操作」。

## 10. 待機回覆規範（Conventions）

- 被指定本輪無須動作的 AI，回覆限制為單行。
- 不重複描述上下文，不延伸提案。
- 此規範目前為協作約定（convention），非系統強制靜音。

## 11. 提交與追溯

- 建議每個完成回合建立 checkpoint（commit 或等效紀錄）。
- 訊息格式建議：`[Executor] fix: <short summary>`
- 回合結束需保留：變更檔案清單、驗證結果、已知風險。

## 12. 後續討論（Backlog）

- 軟超時告警（Soft Timeout）：動態閾值（`TIMEOUT × 0.5`）；當等待成為實際問題時再實作。
- 是否建立半自動驗證腳本（語法檢查 + JSON + UTF-8）。
- 是否將本 SOP 精簡同步到 `AGENTS.md`。
- 狀態摘要錨點（Status Summary Anchor）：待上下文遺失成為實際瓶頸時，再定義寫入時機、欄位格式、保留策略。
- Runtime 模式切換指令（例如 `/mode full`），省去重啟切換 `RELAY_MODE` 的操作成本。

---

版本：`v0.3`  
狀態：已整合六項議題決議並同步至程式碼  
最後更新：2026-03-05
