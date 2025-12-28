# ぴーごの厨房 (Pigo’s Kitchen)

ChatGPT（愛称「ぴーご」）にアップロードするデータを、**“食べやすい形”** に整えるためのCLIツールです。  
大量のソースコードやドキュメントを **効率よく分割**・**除外**しながら、Markdown/TXTとして書き出せます。

- コア機能：`dirdump.py`（汎用ツール）
- ラッパー：`cook.py`（厨房向けの引数・デフォルト・除外パス正規化を担当）

---

## 開発経緯

- 僕の使っているChatGPT（愛称が **ぴーご** ）に **できるだけ食べやすいデータ** を作りたかった。
- ChatGPTではアップロードできるファイルに **トークン制限** があるため、ファイルを **効率よく分割** したかった。

---

## できること

- 指定ディレクトリ以下のテキスト系ファイルを収集して1つの出力にまとめる
- 出力を **サイズで分割**（MB / bytes）
- `vendor` や `node_modules` などの **除外**（名前 or パス）
- 出力形式を `md` / `txt` で切り替え
- 既存の `dirdump.py` は汎用のまま温存し、`cook.py` 側で “厨房っぽい” 使い方に寄せる
※Git管理は必須ではありませんが、出力の再現性や不要ファイル混入を避けるため、Git管理（および適切な除外設定）を推奨します。

---

## 使い方（クイックスタート）

同じフォルダに `cook.py` と `dirdump.py` がある前提です。

```bash
python cook.py dish food serve --recipe md --portion-mb 8
```

例：プロジェクト全体を 8MB 分割で Markdown 化して出力

```bash
python cook.py ~/Desktop/project . ~/Desktop/project_dump.md --portion-mb 8
```

---

## 位置引数（厨房ワード）

`cook.py` は、位置引数の表示を厨房ワードにしています（中身は project/target/out です）。

- `dish`：プロジェクトルート（project）
- `food`：対象ディレクトリ（target）
- `serve`：出力ファイル（out）

```bash
python cook.py dish food serve [options]
```

---

## オプション一覧（厨房ワード + 互換エイリアス）

`cook.py` は厨房ワードを基本にしつつ、`dirdump.py` の互換オプションも受け付けます。

| cook（厨房） | 別名（互換） | 意味 |
|---|---|---|
| `--recipe md|txt` | `--format` | 出力形式 |
| `--portion-mb N` | `--split-mb` | N MBで分割 |
| `--portion-bytes N` | `--split-bytes` | N bytesで分割 |
| `--ingredients a,b,c` | `--ext` | 拡張子フィルタ |
| `--all-ingredients` | `--all-text` | テキスト系を広く対象に |
| `--discard a,b,c` | `--exclude` | 除外（名前/パス） |
| `--forage` | `--all-files` | 全ファイル探索 |
| `--max-bite N` | `--max-bytes` | N bytes超を無視 |
| `--no-menu` | `--no-structure` | ツリー構造出力なし |
| `--menu-max N` | `--structure-max` | ツリー構造の件数上限 |

---

## よく使う例

### 1) 8MBで分割しつつ、よくある不要物を除外

```bash
python cook.py . . out.md \
  --recipe md --portion-mb 8 \
  --discard vendor,node_modules,.git,var,storage,bootstrap/cache
```

### 2) “パスで除外” を雑に書いても、cook側で吸収（正規化）して投げる

```bash
python cook.py ~/Desktop/project . out.md \
  --discard scareer/src/AdminBundle/Resources/public/assets,src/Legacy,vendor
```

> `dirdump.py` は “target からの相対” を前提に除外判定することがあるため、  
> `cook.py` はここを **できるだけ吸収** して `--exclude` に渡します。

### 3) 互換オプションでも動く（どっちで書いてもOK）

```bash
python cook.py . . out.md --format md --split-mb 8 --exclude vendor
```

---

## ChatGPTにアップロードする時のコツ

- いきなり巨大ファイルを投げるより、**8MB程度で分割**しておくと扱いやすいことが多いです
- 画像・バイナリ・巨大ログなどは `--discard` で除外すると精度も速度も上がりやすいです
- 生成された分割ファイルは、必要な範囲から順番にアップロードすると会話が安定します

---

## 仕組み（ざっくり）

- `cook.py` は **厨房ワードの引数**を受け取る
- 必要に応じて `--discard`（除外）などを **正規の形に補正**
- `dirdump.py` に **正規オプション**で投げて実行する

---

## リポジトリ構成（例）

```
.
├── cook.py
├── dirdump.py
└── README.md
```

---

## ライセンス

MIT License

Copyright (c) 2025 Toshiyuki Takeda

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## 免責

このツールは **ChatGPTに直接接続**しません。  
あくまで「アップロードしやすい形に整形する」ためのローカルCLIです。
