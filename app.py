# app.py — Streamlit × Supabase（ユーザーごとに記録を分離）
# -------------------------------------------------------------

# ========== Imports ==========
from supabase import create_client
from postgrest import APIError
import streamlit as st
import pandas as pd
import altair as alt
import datetime as dt
import os, json, shutil

import re, unicodedata

def normalize_email(s: str) -> str:
    s = unicodedata.normalize("NFKC", (s or "").strip())
    return s.strip("()（）<>『』「」")

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


# ========== Page Config ==========
st.set_page_config(page_title="筋トレメモ & 1RMトラッカー", layout="wide")

# ========== Constants / Settings ==========
DATA_DIR = "."
EX_JSON = os.path.join(DATA_DIR, "exercises.json")
DEFAULT_EX = {
    "足":   ["Squat", "Deadlift", "Leg Press"],
    "胸":   ["Bench Press", "Incline Bench Press", "Dips"],
    "背中": ["Barbell Row", "Pull-up", "Lat Pulldown"],
    "肩":   ["Overhead Press", "Lateral Raise"],
    "腕":   ["Barbell Curl", "Triceps Pushdown"],
    "未分類": []
}
EPS = 0.1  # PR判定の許容誤差(kg)

# ========== Utils ==========
def est_1rm_epley(weight, reps):
    """Epley式で1RM推定"""
    if pd.isna(weight) or pd.isna(reps) or reps <= 0:
        return None
    return float(weight) * (1.0 + float(reps) / 30.0)

def _dedup_keep_order(seq):
    seen, out = set(), []
    for x in seq:
        x = (x or "").strip()
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out

def load_ex_master():
    if not os.path.exists(EX_JSON):
        save_ex_master(DEFAULT_EX)
        return DEFAULT_EX.copy()
    try:
        with open(EX_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in DEFAULT_EX.keys():
            data.setdefault(k, [])
            data[k] = _dedup_keep_order(data[k])
        return data
    except Exception:
        return DEFAULT_EX.copy()

def save_ex_master(data):
    with open(EX_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def backup_before_master_edit():
    os.makedirs(os.path.join(DATA_DIR, "backup"), exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        shutil.copy2(EX_JSON, os.path.join(DATA_DIR, "backup", f"exercises_{ts}.json"))
    except Exception:
        pass
    
def y_domain(series, pad_ratio=0.05):
    """系列の最小/最大から少し余白を付けた表示範囲を返す"""
    s = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    if s.empty:
        return None
    lo, hi = float(s.min()), float(s.max())
    span = hi - lo
    if span == 0:  # 全点同値のときは見えるだけの余白
        span = max(1.0, abs(hi)) * 0.1
    pad = span * pad_ratio
    return [lo - pad, hi + pad]



# ========== Supabase Client ==========
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_ANON_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# セッション初期化＆トークンがあるときだけ付与（Noneでは呼ばない）
st.session_state.setdefault("user", None)
st.session_state.setdefault("access_token", None)
_token = st.session_state.get("access_token")
if _token:
    supabase.postgrest.auth(_token)


# ========== Auth UI ==========
with st.sidebar:
    st.subheader("ログイン")
    if st.session_state["user"] is None:
        email = st.text_input("Email")
        pwd   = st.text_input("Password", type="password")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Sign in"):
                li_email = normalize_email(li_email_raw)
                try:
                    li_email.encode("ascii")  # 全角が混ざっていたらここで検出
                except UnicodeEncodeError:
                    st.error("メールアドレスに全角が含まれています。半角で入力してください。")
                    st.stop()
                if not EMAIL_RE.match(li_email):
                    st.error("メール形式が正しくありません（例: name@example.com）。")
                    st.stop()
            
                auth = supabase.auth.sign_in_with_password({"email": li_email, "password": li_pwd})
                st.session_state["user"] = auth.user
                st.session_state["access_token"] = auth.session.access_token
                supabase.postgrest.auth(auth.session.access_token)
                st.success("ログインしました")
                st.rerun()

                
        with c2:
            su_email_raw = st.text_input("Email（新規作成）", key="su_email")
            su_pwd       = st.text_input("Password", type="password", key="su_pwd")
        
            if st.button("Create account"):
                # 全角対策＋簡易バリデーション
                su_email = normalize_email(su_email_raw)
                try:
                    su_email.encode("ascii")  # 全角が混ざっていればここで検出
                except UnicodeEncodeError:
                    st.error("メールアドレスに全角文字が含まれています。半角で入力してください。")
                    st.stop()
        
                if not EMAIL_RE.match(su_email):
                    st.error("メールアドレスの形式が正しくありません（例: name@example.com）。")
                    st.stop()
        
                try:
                    res = supabase.auth.sign_up({"email": su_email, "password": su_pwd})
                    if res.user:
                        st.success("アカウント作成に成功。確認メールが有効な設定なら、受信メールのリンクを開いてください。")
                except Exception as e:
                    st.error(f"サインアップ失敗: {getattr(e, 'message', str(e))}")
    else:
        st.write(f"ユーザー: {st.session_state['user'].email}")
        if st.button("Sign out"):
            supabase.auth.sign_out()
            st.session_state["user"] = None
            st.session_state["access_token"] = None
            st.rerun()

# 未ログインならここで止める
if st.session_state["user"] is None:
    st.stop()

USER_ID = st.session_state["user"].id  # 以降のDB I/Oで使う

# ========== DB I/O ==========
def _iso(v):
    """date/datetimeをISO文字列へ。その他はそのまま"""
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=dt.timezone.utc)
        return v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    return v

def db_load_sets(user_id):
    res = supabase.table("workouts").select("*").eq("user_id", user_id).order("date").execute()
    df = pd.DataFrame(res.data)
    if df.empty: return df
    # 型整備
    df["date"] = pd.to_datetime(df["date"]).dt.date
    if "ts" in df.columns: df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    for c in ["set_no", "reps"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    if "weight_kg" in df.columns: df["weight_kg"] = pd.to_numeric(df["weight_kg"], errors="coerce")
    df["note"] = df.get("note", "").astype(str).fillna("")
    # 1RM列
    df["e1rm"] = df.apply(lambda r: est_1rm_epley(r.get("weight_kg"), r.get("reps")), axis=1)
    return df

def db_insert_set(user_id, row: dict):
    clean = {k: _iso(v) for k, v in row.items()}
    supabase.table("workouts").insert({**clean, "user_id": user_id}).execute()

def db_load_bw(user_id):
    res = supabase.table("bodyweight").select("*").eq("user_id", user_id).order("date").execute()
    df = pd.DataFrame(res.data)
    if df.empty: return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["bodyweight_kg"] = pd.to_numeric(df["bodyweight_kg"], errors="coerce")
    return df

def db_insert_bw(user_id, row: dict):
    clean = {k: _iso(v) for k, v in row.items()}
    supabase.table("bodyweight").insert({**clean, "user_id": user_id}).execute()

# ========== Initial Load ==========
ex_master = load_ex_master()
sets = db_load_sets(USER_ID)
bw   = db_load_bw(USER_ID)

# 部位が変わったらメニュー選択をリセットして先頭に合わせる
def _on_bp_change():
    bp_now = st.session_state.get("bp_main")
    opts = ex_master.get(bp_now, [])
    st.session_state["ex_sel_value"] = opts[0] if opts else ""  # 先頭に合わせる
    st.session_state["ex_new_value"] = ""                       # 新規入力もクリア


# ========== Sidebar: マスター管理（部位・メニュー） ==========
st.sidebar.divider()
st.sidebar.header("マスター管理（部位・メニュー）")

with st.sidebar.expander("部位の管理"):
    new_bp = st.text_input("新しい部位名を追加", key="bp_add")
    if st.button("部位を追加", key="bp_add_btn"):
        name = (new_bp or "").strip()
        if name:
            backup_before_master_edit()
            ex_master.setdefault(name, [])
            save_ex_master(ex_master)
            st.success(f"部位「{name}」を追加しました。")

    bp_old = st.selectbox("改名する部位", options=list(ex_master.keys()), key="bp_old")
    bp_new = st.text_input("新しい部位名", key="bp_new")
    if st.button("部位名を変更", key="bp_rename_btn"):
        old, new = bp_old, (bp_new or "").strip()
        if new and old in ex_master:
            backup_before_master_edit()
            ex_master[new] = ex_master.pop(old)
            save_ex_master(ex_master)
            st.success(f"部位名を「{old}」→「{new}」に変更しました。")

    bp_del = st.selectbox("削除する部位", options=list(ex_master.keys()), key="bp_del")
    if st.button("部位を削除", key="bp_del_btn"):
        if bp_del in ex_master and bp_del != "未分類":
            backup_before_master_edit()
            ex_master["未分類"] = _dedup_keep_order(ex_master.get("未分類", []) + ex_master[bp_del])
            ex_master.pop(bp_del, None)
            save_ex_master(ex_master)
            st.success(f"部位「{bp_del}」を削除（メニューは未分類へ退避）しました。")
        else:
            st.warning("「未分類」は削除できません。")

with st.sidebar.expander("メニューの管理"):
    bp_sel = st.selectbox("対象の部位", options=list(ex_master.keys()), key="bp_sel_ops")
    ex_list = ex_master.get(bp_sel, [])

    ex_add = st.text_input("新規メニューを追加", key="ex_add")
    if st.button("メニューを追加", key="ex_add_btn"):
        name = (ex_add or "").strip()
        if name:
            backup_before_master_edit()
            ex_master[bp_sel] = _dedup_keep_order(ex_list + [name])
            save_ex_master(ex_master)
            st.success(f"「{bp_sel}」に「{name}」を追加しました。")

    ex_old = st.selectbox("改名するメニュー", options=ex_list, key="ex_old")
    ex_new = st.text_input("新しいメニュー名", key="ex_new")
    if st.button("メニュー名を変更", key="ex_rename_btn"):
        new = (ex_new or "").strip()
        if ex_old and new:
            backup_before_master_edit()
            ex_master[bp_sel] = [new if x == ex_old else x for x in ex_master[bp_sel]]
            ex_master[bp_sel] = _dedup_keep_order(ex_master[bp_sel])
            save_ex_master(ex_master)
            st.success(f"メニュー名を「{ex_old}」→「{new}」に変更しました。")

    ex_mv = st.selectbox("移動するメニュー", options=ex_list, key="ex_mv")
    mv_to = st.selectbox("移動先の部位", options=[k for k in ex_master.keys() if k != bp_sel], key="mv_to")
    if st.button("移動", key="ex_move_btn"):
        if ex_mv and mv_to:
            backup_before_master_edit()
            ex_master[bp_sel] = [x for x in ex_master[bp_sel] if x != ex_mv]
            ex_master[mv_to]  = _dedup_keep_order(ex_master.get(mv_to, []) + [ex_mv])
            save_ex_master(ex_master)
            st.success(f"「{ex_mv}」を「{bp_sel}」→「{mv_to}」へ移動しました。")

    ex_del = st.multiselect("削除するメニュー（ログは残す）", options=ex_list, key="ex_del")
    if st.button("メニューを削除", key="ex_del_btn"):
        if ex_del:
            backup_before_master_edit()
            ex_master[bp_sel] = [x for x in ex_list if x not in set(ex_del)]
            save_ex_master(ex_master)
            st.success("メニューを削除しました。（過去の記録は残ります）")

# オプション：DBの現在データをCSVでDL（“今見てる自分のデータ”を書き出す）
st.sidebar.download_button(
    "workouts.csv をダウンロード",
    data=db_load_sets(USER_ID).to_csv(index=False).encode("utf-8"),
    file_name="workouts.csv", mime="text/csv"
)
st.sidebar.download_button(
    "bodyweight.csv をダウンロード",
    data=db_load_bw(USER_ID).to_csv(index=False).encode("utf-8"),
    file_name="bodyweight.csv", mime="text/csv"
)

# ========== Main ==========
st.title("筋トレメモ & 1RMトラッカー")
st.caption("各セットで1RM表示。各メニューの当日最大セットは赤字。過去最高更新はPRハイライト。")

colL, colR = st.columns([1,1])

# ---- 入力フォーム：セット ----
with colL:
    st.subheader("セットの追加（メモ）")

    # --- フォーム外：ここは変更時に即リランされる ---
    date = st.date_input("日付", value=dt.date.today(), key="set_date")

    # 部位を外に出す → 変えた瞬間にメニュー候補が更新される
    bp   = st.selectbox("部位", options=list(ex_master.keys()), key="bp_main")

    # この部位の候補だけ取得
    ex_opts = ex_master.get(bp, [])

    # --- フォーム内：送信ボタンで確定する値だけ ---
    with st.form("add_set", clear_on_submit=True):
        # 部位が変わると key が変わるので旧状態に引っ張られない
        ex_sel = st.selectbox(
            "メニュー（既存）",
            options=ex_opts if ex_opts else [],
            key=f"ex_sel_{bp}"
        )
        ex_new = st.text_input(
            "新規メニュー名（この部位に追加）",
            value="",
            key=f"ex_new_{bp}"
        )
        exercise = ex_new.strip() if ex_new.strip() else ex_sel

        # ==== 前回のトレーニング記録（同メニュー／選択日より前で最新） ====
        if exercise:
            prev_mask = (sets["exercise"] == exercise) & (sets["date"] < date)
            if not sets.empty and prev_mask.any():
                prev_day = sets.loc[prev_mask, "date"].max()
                prev_df = sets[(sets["exercise"] == exercise) & (sets["date"] == prev_day)].copy()
                prev_df["e1rm"] = prev_df.apply(lambda r: est_1rm_epley(r["weight_kg"], r["reps"]), axis=1)
                prev_df = (prev_df
                        .sort_values("set_no")
                        [["set_no","weight_kg","reps","e1rm","note"]]
                        .rename(columns={
                            "set_no":"セット", "weight_kg":"重量(kg)",
                            "reps":"回数", "e1rm":"1RM(kg)", "note":"メモ"
                        }))
                prev_best = prev_df["1RM(kg)"].max()
                st.markdown(f"**前回（{prev_day}）の記録**　セッション1RM: **{prev_best:.1f} kg**")
                st.dataframe(prev_df, hide_index=True, use_container_width=True)
            else:
                st.caption("前回の記録：なし（このメニューは初回）")


        # ==== 前回のトレーニング記録（同メニュー／選択日より前で最新） ====
        prev_mask = (sets["exercise"] == exercise) & (sets["date"] < date)
        if not sets.empty and prev_mask.any():
            prev_day = sets.loc[prev_mask, "date"].max()
            prev_df = sets[(sets["exercise"] == exercise) & (sets["date"] == prev_day)].copy()
            prev_df["e1rm"] = prev_df.apply(lambda r: est_1rm_epley(r["weight_kg"], r["reps"]), axis=1)
            prev_df = (prev_df
                    .sort_values("set_no")
                    [["set_no","weight_kg","reps","e1rm","note"]]
                    .rename(columns={
                        "set_no":"セット","weight_kg":"重量(kg)",
                        "reps":"回数","e1rm":"1RM(kg)","note":"メモ"
                    }))
            prev_best = prev_df["1RM(kg)"].max()
            st.markdown(f"**前回（{prev_day}）の記録**　セッション1RM: **{prev_best:.1f} kg**")
            st.dataframe(prev_df, hide_index=True, use_container_width=True)
        else:
            st.caption("前回の記録：なし（このメニューは初回）")

        # ==== 前回の“最後のセット”をデフォルト入力に反映 ====
        last_w, last_r = 0.0, 1
        if not sets.empty and prev_mask.any():
            _last = (sets[(sets["exercise"]==exercise) & (sets["date"]==prev_day)]
                    .sort_values("set_no").tail(1))
            if not _last.empty:
                last_w = float(_last["weight_kg"].iloc[0] or 0.0)
                last_r = int(_last["reps"].iloc[0] or 1)

        # ==== 同じ日×同じメニューの次セット番号を自動採番 ====
        try:
            exist = sets[(sets["date"] == date) & (sets["exercise"] == exercise)]
            cur_max = pd.to_numeric(exist["set_no"], errors="coerce").max()
            next_set_no = int(cur_max) + 1 if pd.notna(cur_max) else 1
        except Exception:
            next_set_no = 1
        st.caption(f"今回のセット番号: **{next_set_no}**（自動採番）")

        # ==== 入力欄（前回の値をデフォルトにセット） ====
        weight = st.number_input("重量 (kg)", min_value=0.0, step=2.5, value=last_w, key="w_input")
        reps   = st.number_input("回数 (rep)", min_value=1, step=1,   value=last_r, key="r_input")
        note   = st.text_input("感想・メモ（任意）", key="note_input")


        submitted = st.form_submit_button("追加")
        if submitted:
            row = {
                "date": date,  # db_insert_setでISO化
                "exercise": exercise,
                "bodypart": bp,
                "set_no": int(next_set_no),
                "weight_kg": float(weight),
                "reps": int(reps),
                "note": note,
                "ts": dt.datetime.now(dt.timezone.utc)
            }
            try:
                db_insert_set(USER_ID, row)
            except APIError as e:
                st.error(f"DBエラー: {getattr(e, 'message', e)}"); st.stop()
            except Exception as e:
                st.error(f"想定外のエラー: {e}"); st.stop()

            # 新規メニューはマスターにも追加
            if ex_new.strip():
                ex_master[bp] = _dedup_keep_order(ex_master.get(bp, []) + [exercise])
                save_ex_master(ex_master)

            st.success("セットを追加しました。")
            st.rerun()

# 右カラム：体重の記録（消えていたらこれを挿入）
with colR:
    st.subheader("体重の記録")
    with st.form("add_bw", clear_on_submit=True):
        bw_date = st.date_input("日付（体重）", value=dt.date.today(), key="bw_date")
        bw_val  = st.number_input("体重 (kg)", min_value=0.0, step=0.1, value=0.0, key="bw_val")
        bw_sub  = st.form_submit_button("体重を記録")
        if bw_sub:
            try:
                db_insert_bw(USER_ID, {"date": bw_date, "bodyweight_kg": float(bw_val)})
            except APIError as e:
                st.error(f"DBエラー: {getattr(e, 'message', e)}"); st.stop()
            except Exception as e:
                st.error(f"想定外のエラー: {e}"); st.stop()
            st.success("体重を記録しました。")
            st.rerun()


# ---- 最新データを再ロード（DBから） ----
sets = db_load_sets(USER_ID)
bw   = db_load_bw(USER_ID)

# ========== Views ==========
# 当日のセット一覧（色付け & PR）
st.divider()
st.subheader("当日のセット一覧（色付け & PR）")
day = st.date_input("表示する日付", value=dt.date.today(), key="view_day")
today_sets = sets[sets["date"] == day].copy()

if today_sets.empty:
    st.info("この日付の記録はありません。上のフォームで追加してください。")
else:
    today_sets["e1rm"] = today_sets.apply(lambda r: est_1rm_epley(r["weight_kg"], r["reps"]), axis=1)
    history = sets[sets["date"] < day].copy()
    if not history.empty:
        history["e1rm"] = history.apply(lambda r: est_1rm_epley(r["weight_kg"], r["reps"]), axis=1)
    best_hist = (history.dropna(subset=["e1rm"])
                        .sort_values(["exercise","e1rm"], ascending=[True, False])
                        .groupby("exercise", as_index=True)
                        .first()[["e1rm"]]
                        .rename(columns={"e1rm":"hist_best"}))

    for ex in sorted(today_sets["exercise"].unique()):
        ex_df = today_sets[today_sets["exercise"] == ex].sort_values("set_no")

        # 当日の最大1RM（セッション1RM）
        max_e1rm = ex_df["e1rm"].max()
        st.markdown(f"### {ex}（当日セッション1RM: **{max_e1rm:.1f} kg**）")

        # 過去最高
        hist_best_val = best_hist.loc[ex, "hist_best"] if ex in best_hist.index else None
        # 当日がPR更新日か？（当日の最大で判定）
        is_pr_day = (hist_best_val is None) or (max_e1rm > (hist_best_val + EPS))
        # 当日の最大セットを1つに限定（同値が複数でも最初の1つ）
        best_idx = ex_df["e1rm"].idxmax()

        for idx, row in ex_df.iterrows():
            e1 = row["e1rm"]
            is_session_best = (idx == best_idx)
            color = "red" if is_session_best else "black"
            show_pr = is_session_best and is_pr_day  # 最大セットかつPR更新日のみ

            pr_badge = " 🏆 **PR更新**" if show_pr else ""
            st.markdown(
                f"- セット{int(row['set_no'])}: {row['weight_kg']} kg × {int(row['reps'])} rep "
                f"｜ 1RM推定: <span style='color:{color}'><strong>{e1:.1f} kg</strong></span>{pr_badge} "
                f"｜ メモ: {row['note']}",
                unsafe_allow_html=True
            )

# 日内：セットごとの1RM推移
st.divider()
st.subheader("日内：セットごとの1RM推移（休憩目安）")
if today_sets.empty:
    st.info("この日付の記録はありません。")
else:
    day_exercises = sorted(today_sets["exercise"].unique().tolist())
    sel_ex = st.multiselect("対象メニューを選択", options=day_exercises,
                            default=day_exercises[:1] if day_exercises else [])
    if not sel_ex:
        st.info("メニューを選んでください。")
    else:
        view = today_sets[today_sets["exercise"].isin(sel_ex)].copy()
        # 記録時刻から休憩推定
        if "ts" in view.columns and view["ts"].notna().any():
            view = view.sort_values(["exercise", "ts", "set_no"])
            view["rest_min"] = (view.groupby("exercise")["ts"].diff().dt.total_seconds() / 60).round(1)
        else:
            view = view.sort_values(["exercise", "set_no"])

        dom_e1 = y_domain(view["e1rm"])
        y_enc = alt.Y(
            "e1rm:Q", title="1RM (kg)",
            scale=alt.Scale(domain=dom_e1, zero=False, nice=False)
        )

        chart = alt.Chart(view).mark_line(point=True).encode(
            x=alt.X("set_no:Q", title="セット番号", axis=alt.Axis(format="d", tickMinStep=1)),
            y=y_enc,  # ← ここを差し替え
            color=alt.Color("exercise:N", title="メニュー"),
            tooltip=[
                alt.Tooltip("exercise:N", title="メニュー"),
                alt.Tooltip("set_no:Q",   title="セット", format=".0f"),
                alt.Tooltip("weight_kg:Q",title="重量(kg)"),
                alt.Tooltip("reps:Q",     title="回数"),
                alt.Tooltip("e1rm:Q",     title="1RM(kg)", format=".1f"),
            ] + ([alt.Tooltip("rest_min:Q", title="休憩(分)", format=".1f")]
                if "rest_min" in view.columns else [])
        )
        st.altair_chart(chart, use_container_width=True)
        if "rest_min" in view.columns:
            st.caption("※ 休憩時間は各セットの記録時刻の差分から推定（目安）。")

# メニュー別：セッション最大1RM 推移
st.divider()
st.subheader("メニュー別：セッション最大1RM の推移")
if sets.empty:
    st.info("データがありません。")
else:
    tmp = sets.copy()
    tmp["e1rm"] = tmp.apply(lambda r: est_1rm_epley(r["weight_kg"], r["reps"]), axis=1)
    ses = (tmp.dropna(subset=["e1rm"])
              .groupby(["date","exercise"], as_index=False)["e1rm"].max()
              .rename(columns={"e1rm":"session_1rm"}))

    ex_opts = sorted(ses["exercise"].unique().tolist())
    chosen_ex = st.multiselect("表示するメニュー", options=ex_opts, default=ex_opts[:1] if ex_opts else [])
    if not chosen_ex:
        st.info("表示するメニューを選んでください。")
    else:
        base = ses[ses["exercise"].isin(chosen_ex)].copy()
        dom_ses = y_domain(base["session_1rm"])  # ← ヘルパーで最小最大+少し余白
        y_enc = alt.Y(
            "session_1rm:Q",
            title="1RM (kg)",
            scale=alt.Scale(domain=dom_ses, zero=False, nice=False)
        )

        line = alt.Chart(base).mark_line(point=True).encode(
            x=alt.X("date:T", title="日付"),
            y=y_enc,  # ← ここを差し替え
            color=alt.Color("exercise:N", title="メニュー")
        )
        st.altair_chart(line, use_container_width=True)

# オーバーレイ：体重 or 別メニュー1RM
st.divider()
st.subheader("オーバーレイ：体重 または 別メニューの1RMを重ねる")
if sets.empty:
    st.info("データがありません。")
else:
    tmp = sets.copy()
    tmp["e1rm"] = tmp.apply(lambda r: est_1rm_epley(r["weight_kg"], r["reps"]), axis=1)
    ses = (tmp.dropna(subset=["e1rm"])
              .groupby(["date","exercise"], as_index=False)["e1rm"].max()
              .rename(columns={"e1rm":"session_1rm"}))

    c1, c2, c3 = st.columns(3)
    with c1:
        base_ex = st.selectbox("メイン軸：メニュー（1RM）", options=sorted(ses["exercise"].unique()) if not ses.empty else [])
    with c2:
        overlay_mode = st.selectbox("重ねる対象", options=["体重", "別メニューの1RM"])
    with c3:
        overlay_ex = None
        if overlay_mode == "別メニューの1RM":
            overlay_ex = st.selectbox("重ねるメニュー", options=[e for e in sorted(ses["exercise"].unique()) if e != base_ex])

    if base_ex:
        base_df = ses[ses["exercise"] == base_ex][["date","session_1rm"]].rename(columns={"session_1rm": f"{base_ex}_1RM"})
    else:
        base_df = pd.DataFrame(columns=["date", f"{base_ex}_1RM"])

    if overlay_mode == "体重":
        if bw.empty:
            st.warning("体重データがありません。右側のフォームから体重を記録してください。")
        else:
            bw_series = bw[["date","bodyweight_kg"]].copy()
            dom_base = y_domain(base_df[f"{base_ex}_1RM"])
            dom_bw   = y_domain(bw_series["bodyweight_kg"])
            y_base = alt.Y(f"{base_ex}_1RM:Q", title=f"{base_ex} 1RM (kg)",
                        axis=alt.Axis(titleColor="#1f77b4"),
                        scale=alt.Scale(domain=dom_base, zero=False, nice=False))
            y_bw = alt.Y("bodyweight_kg:Q", title="体重 (kg)",
                        axis=alt.Axis(titleColor="#ff7f0e")),
                        # ↓ scaleを別行に（見やすさのため）
            y_bw = alt.Y("bodyweight_kg:Q", title="体重 (kg)",
                        axis=alt.Axis(titleColor="#ff7f0e"),
                        scale=alt.Scale(domain=dom_bw, zero=False, nice=False))

            chart = alt.layer(
                alt.Chart(base_df).mark_line(point=True).encode(
                    x=alt.X("date:T", title="日付"),
                    y=y_base
                ),
                alt.Chart(bw_series).mark_line(point=True).encode(
                    x=alt.X("date:T"),
                    y=y_bw,
                    color=alt.value("#ff7f0e")
                )
            ).resolve_scale(y='independent')

            st.altair_chart(chart, use_container_width=True)
    else:
        if overlay_ex is None:
            st.info("重ねるメニューを選んでください。")
        else:
            ov = ses[ses["exercise"] == overlay_ex][["date","session_1rm"]].rename(columns={"session_1rm": f"{overlay_ex}_1RM"})
            
            dom_base = y_domain(base_df[f"{base_ex}_1RM"])
            dom_ov   = y_domain(ov[f"{overlay_ex}_1RM"])
            y_base = alt.Y(f"{base_ex}_1RM:Q", title=f"{base_ex} 1RM (kg)",
                        axis=alt.Axis(titleColor="#1f77b4"),
                        scale=alt.Scale(domain=dom_base, zero=False, nice=False))
            y_ov = alt.Y(f"{overlay_ex}_1RM:Q", title=f"{overlay_ex} 1RM (kg)",
                        axis=alt.Axis(titleColor="#ff7f0e"),
                        scale=alt.Scale(domain=dom_ov, zero=False, nice=False))

            
            chart = alt.layer(
                alt.Chart(base_df).mark_line(point=True).encode(
                    x=alt.X("date:T", title="日付"),
                    y=y_base
                ),
                alt.Chart(ov).mark_line(point=True).encode(
                    x=alt.X("date:T"),
                    y=y_ov,
                    color=alt.value("#ff7f0e")
                )
            ).resolve_scale(y='independent')


            st.altair_chart(chart, use_container_width=True)

st.caption("v1.1 DB版：ユーザーごとに完全分離（Supabase Auth + RLS）。入力→DB保存→再描画まで統一。")

