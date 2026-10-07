# 連絡用ファイル（ChatGPT 連携）

このファイルは ChatGPT との疎通専用です。作業者はここに報告を記載し、ChatGPT はこのファイルを読んで次の指示を出します。
コード未完成の状態で commit しなくても、このファイルで状況共有できます。

最終更新: 2026-10-08 / 対応コミット: `a030515`（`wip: step 18-r2 ranking analysis`・状態記録）

---

## 現在の状態

- Branch: `main`
- 最新コミット: `a030515` (`wip: step 18-r2 ranking analysis`)
- Working tree: prompt.py 1箇所＋docs（未コミット・記録予定）
- 進行中ステップ: **Step 18-R2**（§8 分析完了・コード修正済み・70評価は Quota 待ち）

## Step 18-R2 状態

- §8 ranking 分析: Top-1≠max-Conv 29件の内訳は forced 0・mild 9・div 3・nat 17。ranking bug なし（Context 優先は正しい順位付け）
- 修正: 短＋話題の組合せ例を1箇所追加（pytest 287 passed）。Quota 枯渇で70未測定
- 詳細は `docs/development/current-generation-analysis.md` の `## Step 18-R2` を参照

## Step 18-R1 結果（履歴）

- R6 vs 18 の10悪化ケースを全件比較: b08 の AI_QUESTION 戦略反転＋言い換え変動。adaptation は synthetic で不活性（単体テストで証明）と確定
- §4 遵守（tone ±0.02 は最小項・変更なし）。§5 seeded 重み実験（全 weight で Top-1 安定・0.02 適切・変更なし）
- Brevity cap 試行→接触別 3/3→1/3 に破壊→§10 に従い revert。製品コード変更ゼロ
- 70ケース: 変更なしのため Step 18 の 5/6 を維持（Conversation 0.956 未達継続）
- 詳細は `docs/development/current-generation-analysis.md` の `## Step 18-R1` を参照

## Step 18 結果（履歴）

- 接触別ベンチ（3 contacts・同一メッセージ）: **3/3 識別**。B の文量適応が R6 比 **8倍改善**（距離12.7→1.6）
- 70ケース（3.1統一）: AI 0.076✓ / Context 0.607✓ / Human **0.908**✓改善 / Conversation 0.956✗ / Questions 0.005✓ / Echo 0.048✓
- **5/6**。Conversation 未達は引き直しノイズ（Step 18 は prompt.py 無変更＝run_live 同一コードのため系統的悪化なし）
- 詳細は `docs/development/current-generation-analysis.md` の `## Step 18` を参照

## Step 17-R6 最終結果（70ケース・3.1統一・履歴）

- 構成: 70件×gemini-3.1-flash-lite
- 210候補・エラー0・parse_ok 1.0・？？使用 0件

| 指標 | 17-R6 | 基準 | 判定 |
|---|---|---|---|
| AI-like | 0.081 | ≤0.098 | ✓ |
| Context Fit | 0.618 | ≥0.605 | ✓ |
| Human | 0.900 | ≥0.843 | ✓ |
| Conversation | 0.969 | ≥0.963 | ✓ |
| Questions | 0.000 | ≤0.059 | ✓ |
| Echo | 0.067 | ≤0.132 | ✓ |

- **最終判定: 合格（6/6）**。自律ループ10 iteration で達成。決定打は？？の例示除去
- 詳細は `docs/development/current-generation-analysis.md` の `## Step 17-R6` を参照

## Step 17-R5 最終結果（70ケース・3.1統一・履歴）

- 構成: 70件×gemini-3.1-flash-lite
- 210候補・エラー0・parse_ok 1.0

| 指標 | 17-R5 | 基準 | 判定 |
|---|---|---|---|
| AI-like | 0.100 | ≤0.098 | ✗僅差 |
| Context Fit | 0.619 | ≥0.605 | ✓ |
| Novel Keyword | 0.586 | —（悪化させない） | 維持（微減） |
| Human | 0.849 | ≥0.843 | ✓ |
| Conversation | 0.961 | ≥0.963 | ✗僅差 |
| Questions | 0.157 | ≤0.059 | ✗悪化 |
| Echo | 0.067 | ≤0.132 | ✓ |

- **最終判定: 不合格（6項目中3達成）**。R5 変更は backfire（悪い例の列挙がコピーを誘発＋緩和でデフォルト回帰）
- 詳細は `docs/development/current-generation-analysis.md` の `## Step 17-R5` を参照

## Step 17-R4 最終結果（70ケース・3.1統一・履歴）

- 構成: 70件×gemini-3.1-flash-lite（§19・§23 の同一モデル評価）
- 210候補・エラー0・parse_ok 1.0

| 指標 | 17-R4 | 基準 | 判定 |
|---|---|---|---|
| AI-like | 0.062 | ≤0.098 | ✓ |
| Context Fit | 0.607 | ≥0.605 | ✓ |
| Novel Keyword | 0.595 | ≤0.436 | ✗ |
| Human | 0.846 | ≥0.843 | ✓僅差 |
| Conversation | 0.950 | ≥0.963 | ✗ |
| Questions | 0.138 | ≤0.059 | ✗悪化 |
| Echo | 0.062 | ≤0.132 | ✓ |

- **最終判定: 不合格（4/7）**。Context は達成（0.589→0.607）も Questions 爆発（制約の締め付け→質問が逃げ道に）
- 詳細は `docs/development/current-generation-analysis.md` の `## Step 17-R4` を参照

## 直近の指標推移（70ケース直接パス・履歴）

## Step 17-R3 最終結果（70ケース完成・履歴）

- 構成: 60件×gemini-3.5-flash-lite＋10件×gemini-3.1-flash-lite（3.5 の 429 回避のため残り10件を 3.1 で実行）
- 210候補・エラー0・parse_ok 1.0

| 指標 | 17-R3 | 基準 | 判定 |
|---|---|---|---|
| AI-like | 0.062 | ≤0.098 | ✓ |
| Context Fit | 0.589 | ≥0.605 | ✗ |
| Novel Keyword | 0.476 | ≤0.436 | ✗ |
| Human | 0.845 | ≥0.843 | ✓僅差 |
| Conversation | 0.960 | ≥0.963 | ✗僅差 |
| Questions | 0.062 | ≤0.059 | ✗僅差 |
| Echo | 0.110 | ≤0.132 | ✓ |

- **最終判定: 不合格（4指標未達）**。コード変更せず記録のみで停止中
- 詳細は `docs/development/current-generation-analysis.md` の `## Step 17-R3` を参照

## 直近の指標推移（70ケース直接パス）

| 指標 | 17 | 17-R | 17-R2 | 17-R3混成 | 17-R4統一3.1 | 17-R5統一3.1 | 17-R6統一3.1 | 18統一3.1 | 基準 |
|---|---|---|---|---|---|---|---|---|---|
| AI-like | 0.119 | 0.043 | 0.048 | 0.062 | 0.062 | 0.100 | 0.081 | 0.076 | ≤0.098 |
| Context Fit | 0.589 | 0.606 | 0.597 | 0.589 | 0.607 | 0.619 | 0.618 | 0.607 | ≥0.605 |
| Novel Keyword | 0.473 | 0.490 | 0.478 | 0.476 | 0.595 | 0.586 | 0.619 | 0.571 | 参考 |
| Human | 0.880 | 0.895 | 0.868 | 0.845 | 0.846 | 0.849 | 0.900 | 0.908 | ≥0.843/0.900 |
| Conversation | 0.969 | 0.974 | 0.977 | 0.960 | 0.950 | 0.961 | 0.969 | 0.956 | ≥0.963 |
| Questions | 0.025 | 0.038 | 0.077 | 0.062 | 0.138 | 0.157 | 0.000 | 0.005 | ≤0.059 |
| Echo | 0.104 | 0.076 | 0.106 | 0.110 | 0.062 | 0.067 | 0.067 | 0.048 | ≤0.132 |

- 17-R: 6/7（Novel のみ未達。実質 0.338 は artifact 除外で基準内）
- 17-R2: 4/7（Context・Novel・Questions 未達。Questions 悪化は C1 の副作用と特定済み）
- 17-R3: 3/7 混成（AI/Human/Echo のみ。Conversation・Questions は僅差未達）
- 17-R4: 4/7 統一3.1（AI/Context/Human/Echo。Context 達成も Questions 爆発・Novel/Conversation 悪化）
- 17-R5: 3/7 統一3.1（Context/Human/Echo。Questions さらに悪化・AI-like も僅差未達に転落）
- 17-R6: **6/6 合格**（？？例示除去で Questions 0.000。Novel は対象外）
- 18: 5/6（Human 改善＋Style Fit 実証。Conversation のみノイズ範囲で未達）
- 18-R1: 製品コード変更ゼロ（§1-5 実行・brevity cap は revert）。5/6 維持

## 既知の分析結果

- Novel 測定は artifact 支配: 笑笑 10件・汎用語（今日/明日/本当/了解 等）22件が混入。artifact 除外の実質値は 0.338
- Questions 悪化（17-R2）: 方向例に「質問」を列挙したためモデルが質問で差別化。17-R3 で修正済み
- pytest 287 passed / npm run build 成功を維持
- 詳細は `docs/development/current-generation-analysis.md` の `## Step 17-R6` / `## Step 18` を参照

## 作業者への質問・次のアクション

1. ~~Quota 回復後、残り10件を実行→60件と結合→70ケース完全版で合否判定~~ → 完了（3.1 で実行し70ケース完成）
2. 18-R1 は製品コード変更ゼロ（§1-5 実行・brevity cap revert）で記録・push 後に停止。ChatGPT の判断待ち（ノイズとして受理／再実行指示／基準調整／rollback）。新規の改善実装は指示があるまで行わない

## ChatGPT への連絡欄

（作業者が報告を追記する場所）

- 2026-10-07: 本ファイル新設。17-R3 は残り10件待ち。Quota 回復後の再実行指示待ち。
- 2026-10-07: 70ケース完成（60×3.5＋10×3.1 混成）。最終判定は不合格（Context/Novel/Conversation/Questions 未達）。コード変更せず停止中。次の指示待ち。
- 2026-10-07: 17-R4 完成（70×3.1統一）。Context 達成（0.607）も Questions 爆発（0.138）・Novel/Conversation 悪化で不合格（4/7）。制約の締め付け分析を記録。`wip: step 17-r4 results` で push 後に停止。次の指示待ち。
- 2026-10-07: 17-R5 完成（70×3.1統一）。Context 維持（0.619）も Questions さらに悪化（0.157）・AI-like 僅差未達転落で不合格（3/7）。悪い例の列挙がコピーを誘発した backfire と特定。R5 追記 revert を推奨。`wip: step 17-r5 results` で push 後に停止。次の指示待ち。
- 2026-10-08: 17-R6 完成（70×3.1統一）。自律ループ10 iteration で **6/6 合格**（AI 0.081/Context 0.618/Human 0.900/Conversation 0.969/Questions 0.000/Echo 0.067）。決定打は？？の例示除去（？？使用 30→0）。pytest 281 passed / build 成功。合格コミットを push 後に停止。確認後 Step 18 の指示待ち。
- 2026-10-08: 18 完成（接触別ベンチ 3/3 識別＋70×3.1統一）。Human 改善（0.908）＋Style Fit 実証（B文量 8倍）。70ケース 5/6（Conversation 0.956 のみノイズ範囲で未達。prompt.py 無変更＝系統的悪化なし）。`wip: step 18 contact adaptation` で push 後に停止。判断待ち。
- 2026-10-08: 18-R1 完成（§1-5 実行）。10悪化ケース比較で引き直し変動と確定（b08 戦略反転）。§4遵守・§5 seeded実験（全weight安定）で変更なし。Brevity cap は 3/3→1/3 破壊のため revert。製品コード変更ゼロ。5/6 維持。docs のみ記録して停止。判断待ち。
- 2026-10-08: 18-R2 開始（§8 ranking分析・短＋話題語修正・pytest 287）。70評価は 3.1 の 429 Quota 枯渇で未完了（20分timeout＋3分待機も回復せず）。wip: step 18-r2 ranking analysis で記録後に停止。Quota 回復後の70実行指示待ち。
- 2026-10-08: 18-R2 継続指示で70再開も Quota が2-3コールで再枯渇。5件のみ成功、65件未完了。10分待機も回復せず停止。コード a030515 固定のまま Quota 回復待ち。
