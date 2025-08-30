#Multi Audio Fade Control a Python OBS-addon script (v1.2)

#ADDON BY: LAGTIME SEEDLING
#https://www.youtube.com/@lagtime__seedling/videos
#YEAR: 2025


#open obs → tools → scripts
#click “+” and add this .py file
#tick “show source manager” to reveal controls
#click “+ add audio source” to create an entry
#set “audio source name” to exactly match the obs source you want to control
#set “fade duration (seconds)” for how long the fade should take
#set “minimum volume (db)” for fade down target
#set “maximum volume (db)” for fade up target
#click “test fade down” or “test fade up” to verify behavior
#repeat “+ add audio source” for as many sources as you need
#each source has independent fade duration, min db, max db, and hotkeys
#open settings → hotkeys and bind “[source name] fade down” and “[source name] fade up”
#rename a source in the scripts panel to update its hotkey labels automatically
#to remove a source entry, click “remove this source” in its group
#changes save automatically; hotkey bindings persist across sessions
#if the ui looks stale, or freezes, reload the script. settings should persist.
#keep min db ≤ max db; the script auto-swaps if you set them backwards
#check help → log files → view current log for debug messages


#Hotkeys: Settings → Hotkeys (search "Multi Audio Fade")
#UI: Tools → Scripts → this script → enable "Show Source Manager"

import obspython as obs
import math
import time
import json
import uuid

#-----------------------------
#Defaults / Config
#-----------------------------

DEFAULT_FADE_DURATION = 2.0
DEFAULT_MIN_DB = -30.0
DEFAULT_MAX_DB = 0.0

DEFAULT_SHOW_MANAGER = True
TIMER_INTERVAL_MS = 16

sources = []          #[{id,name,fade_duration,min_db,max_db}]
active_fades = {}     #sid -> {start_db,target_db,start_time}
hotkey_ids = {}       #sid -> {"down": id, "up": id}
timer_running = False
SHOW_MANAGER = DEFAULT_SHOW_MANAGER
_g_settings = None    #live pointer to script settings for forcing refresh/persist

#-----------------------------
#dB helpers
#-----------------------------
def db_to_linear(db):
    #Snap to exactly 1.0 when target is ~0 dB to avoid UI rounding showing -2 dB
    if db >= -0.05:
        return 1.0
    return math.pow(10.0, db / 20.0)

def linear_to_db(x):
    if x <= 0.0:
        return -80.0
    #Snap exactly 0 dB when linear hits 1.0
    if x >= 0.999999:
        return 0.0
    return 20.0 * math.log10(x)

def clamp_db(db, min_db, max_db):
    return max(min_db, min(max_db, db))

#-----------------------------
#OBS source helpers
#-----------------------------
def get_source_by_name(name):
    return obs.obs_get_source_by_name(name)

def get_volume_db(name):
    src = get_source_by_name(name)
    if src:
        lin = obs.obs_source_get_volume(src)
        db = linear_to_db(lin)
        obs.obs_source_release(src)
        return db
    return DEFAULT_MIN_DB

def set_volume_db(name, db_val):
    src = get_source_by_name(name)
    if src:
        lin = db_to_linear(db_val)
        #hard snap to 1.0 at 0 dB ends (eliminates -2 dB cap)
        if db_val >= -0.05:
            lin = 1.0
        obs.obs_source_set_volume(src, max(0.0, min(1.0, lin)))
        obs.obs_source_release(src)

#-----------------------------
#Model helpers
#-----------------------------
def unique_id():
    return str(uuid.uuid4())

def find_source(sid):
    for s in sources:
        if s["id"] == sid:
            return s
    return None

def serialize_sources():
    return json.dumps(sources)

def deserialize_sources(s):
    try:
        data = json.loads(s)
    except Exception:
        return []
    out = []
    for item in data or []:
        out.append({
            "id": str(item.get("id") or uuid.uuid4()),
            "name": str(item.get("name") or f"ALL_SOUNDS"),
            "fade_duration": float(item.get("fade_duration", DEFAULT_FADE_DURATION)),
            "min_db": float(item.get("min_db", DEFAULT_MIN_DB)),
            "max_db": float(item.get("max_db", DEFAULT_MAX_DB)),
        })
    return out

def persist_sources_and_refresh():
    #Persist to settings and bump a hidden nonce to force property refresh
    if _g_settings is None:
        return
    obs.obs_data_set_string(_g_settings, "sources_json", serialize_sources())
    #bump hidden nonce to notify OBS that "something changed"
    curr = obs.obs_data_get_int(_g_settings, "ui_nonce")
    obs.obs_data_set_int(_g_settings, "ui_nonce", (curr + 1) % 2_000_000_000)

#-----------------------------
#Fade Engine
#-----------------------------
def ensure_timer():
    global timer_running
    if not timer_running and active_fades:
        obs.timer_add(update_fades_timer, TIMER_INTERVAL_MS)
        timer_running = True

def stop_timer_if_idle():
    global timer_running
    if timer_running and not active_fades:
        obs.timer_remove(update_fades_timer)
        timer_running = False

def start_fade_for(sid, target_db):
    src = find_source(sid)
    if not src:
        return
    name = src["name"]
    min_db = float(src["min_db"])
    max_db = float(src["max_db"])
    dur = max(0.01, float(src["fade_duration"]))

    cur = get_volume_db(name)
    tgt = clamp_db(target_db, min_db, max_db)

    #Avoid redundant fades
    if tgt >= max_db and cur >= max_db - 0.5:
        return
    if tgt <= min_db and cur <= min_db + 0.5:
        return

    active_fades[sid] = {"start_db": cur, "target_db": tgt, "start_time": time.time()}
    ensure_timer()

def update_fades_timer():
    now = time.time()
    finished = []

    for sid, state in list(active_fades.items()):
        src = find_source(sid)
        if not src:
            finished.append(sid)
            continue

        dur = max(0.01, float(src["fade_duration"]))
        t = (now - state["start_time"]) / dur

        if t >= 1.0:
            set_volume_db(src["name"], state["target_db"])
            #snap hard to 0 if that's the target
            if state["target_db"] >= -0.05:
                set_volume_db(src["name"], 0.0)
            finished.append(sid)
        else:
            #smoothstep
            p = 3 * (t ** 2) - 2 * (t ** 3)
            cur = state["start_db"] + (state["target_db"] - state["start_db"]) * p
            set_volume_db(src["name"], cur)

    for sid in finished:
        active_fades.pop(sid, None)

    stop_timer_if_idle()

#-----------------------------
#Hotkeys
#-----------------------------
def make_hotkey_down_cb(sid):
    def _cb(pressed):
        if pressed:
            src = find_source(sid)
            if src:
                start_fade_for(sid, float(src["min_db"]))
    return _cb

def make_hotkey_up_cb(sid):
    def _cb(pressed):
        if pressed:
            src = find_source(sid)
            if src:
                start_fade_for(sid, float(src["max_db"]))
    return _cb

def hotkey_label(name, up=False):
    return f"[{name}] Fade {'Up' if up else 'Down'}"

def hotkey_storage_key(sid, up=False):
    return f"hk_{sid}_{'up' if up else 'down'}"

def register_hotkeys_for_source(sid, settings=None):
    if sid in hotkey_ids:
        return
    s = find_source(sid)
    if not s:
        return
    name = s["name"]

    hid_down = obs.obs_hotkey_register_frontend(f"multi_audio_fade_{sid}_down",
                                                hotkey_label(name, up=False),
                                                make_hotkey_down_cb(sid))
    hid_up = obs.obs_hotkey_register_frontend(f"multi_audio_fade_{sid}_up",
                                              hotkey_label(name, up=True),
                                              make_hotkey_up_cb(sid))
    hotkey_ids[sid] = {"down": hid_down, "up": hid_up}

    #Load saved bindings if present
    if settings is not None:
        arr_down = obs.obs_data_get_array(settings, hotkey_storage_key(sid, up=False))
        arr_up = obs.obs_data_get_array(settings, hotkey_storage_key(sid, up=True))
        if arr_down:
            obs.obs_hotkey_load(hid_down, arr_down)
            obs.obs_data_array_release(arr_down)
        if arr_up:
            obs.obs_hotkey_load(hid_up, arr_up)
            obs.obs_data_array_release(arr_up)

def relabel_hotkeys_for_source(sid, new_name):
    """Rename hotkeys to follow source name while preserving bindings."""
    ids = hotkey_ids.get(sid)
    if not ids:
        #Not yet registered (e.g., first add) -> just register
        register_hotkeys_for_source(sid, settings=_g_settings)
        return

    #Save existing bindings
    arr_down = obs.obs_hotkey_save(ids["down"])
    arr_up = obs.obs_hotkey_save(ids["up"])

    #Unregister if supported (older OBS builds may lack this)
    if hasattr(obs, "obs_hotkey_unregister"):
        obs.obs_hotkey_unregister(ids["down"])
        obs.obs_hotkey_unregister(ids["up"])

    #Re-register with new labels
    hid_down = obs.obs_hotkey_register_frontend(f"multi_audio_fade_{sid}_down",
                                                hotkey_label(new_name, up=False),
                                                make_hotkey_down_cb(sid))
    hid_up = obs.obs_hotkey_register_frontend(f"multi_audio_fade_{sid}_up",
                                              hotkey_label(new_name, up=True),
                                              make_hotkey_up_cb(sid))
    hotkey_ids[sid] = {"down": hid_down, "up": hid_up}

    #Restore bindings
    if arr_down:
        obs.obs_hotkey_load(hid_down, arr_down)
        obs.obs_data_array_release(arr_down)
    if arr_up:
        obs.obs_hotkey_load(hid_up, arr_up)
        obs.obs_data_array_release(arr_up)

#-----------------------------
#UI (Properties)
#-----------------------------
def script_description():
    return (
        "Multi Audio Fade Controller by Lagtime Seedling\n\n"
        "- Lets you hotkey an audio source fade.\n"
        "- Hotkeys located in settings>hotkeys as: [Source Name] Fade Up/Down.\n"
        "- You can add as many audio sources as you like, each with its own control hotkey.\n"
    )

def script_properties():
    props = obs.obs_properties_create()

    #Hidden "nonce" to force OBS to refresh properties when we change state
    p_nonce = obs.obs_properties_add_int(props, "ui_nonce", "", 0, 2_000_000_000, 1)
    obs.obs_property_set_visible(p_nonce, False)

    obs.obs_properties_add_bool(props, "show_manager", "Show Source Manager")

    if SHOW_MANAGER:
        #Add Source
        obs.obs_properties_add_button(props, "add_source_btn", "+ Add Audio Source", on_add_clicked)

        #Build per-source groups
        for s in sources:
            sid = s["id"]
            group = obs.obs_properties_create()

            #Name (with live modified callback)
            p_name = obs.obs_properties_add_text(group, f"src_{sid}_name", "Audio Source Name", obs.OBS_TEXT_DEFAULT)
            obs.obs_property_set_modified_callback(p_name, on_name_modified)

            #Fade duration
            p_fd = obs.obs_properties_add_float_slider(group, f"fade_{sid}_duration",
                                                       "Fade Duration (seconds)", 0.1, 30.0, 0.1)
            obs.obs_property_set_modified_callback(p_fd, on_number_modified)

            #Min/Max dB
            p_min = obs.obs_properties_add_float_slider(group, f"min_{sid}_db", "Minimum Volume (dB)", -80.0, -1.0, 1.0)
            obs.obs_property_set_modified_callback(p_min, on_number_modified)

            p_max = obs.obs_properties_add_float_slider(group, f"max_{sid}_db", "Maximum Volume (dB)", -30.0, 0.0, 1.0)
            obs.obs_property_set_modified_callback(p_max, on_number_modified)

            #Test + Remove
            obs.obs_properties_add_button(group, f"test_{sid}_down", "Test Fade Down", on_test_clicked)
            obs.obs_properties_add_button(group, f"test_{sid}_up", "Test Fade Up", on_test_clicked)
            obs.obs_properties_add_button(group, f"remove_{sid}", "Remove This Source", on_remove_clicked)

            label = f"Source: {s.get('name','(unnamed)')}"
            obs.obs_properties_add_group(props, f"group_{sid}", label, obs.OBS_GROUP_NORMAL, group)

    return props

#---- UI callbacks ----
def on_add_clicked(props, prop):
    sid = unique_id()
    sources.append({
        "id": sid,
        "name": f"ALL_SOUNDS_{len(sources)+1}",
        "fade_duration": DEFAULT_FADE_DURATION,
        "min_db": DEFAULT_MIN_DB,
        "max_db": DEFAULT_MAX_DB,
    })
    register_hotkeys_for_source(sid, settings=_g_settings)
    persist_sources_and_refresh()
    return True  #rebuild properties immediately

def on_remove_clicked(props, prop):
    name = obs.obs_property_name(prop)  #"remove_<sid>"
    sid = name.split("remove_", 1)[1]
    active_fades.pop(sid, None)

    #Drop hotkeys
    ids = hotkey_ids.pop(sid, None)
    if ids and hasattr(obs, "obs_hotkey_unregister"):
        obs.obs_hotkey_unregister(ids["down"])
        obs.obs_hotkey_unregister(ids["up"])

    #Remove entry
    for i, s in enumerate(sources):
        if s["id"] == sid:
            sources.pop(i)
            break

    persist_sources_and_refresh()
    return True

def on_test_clicked(props, prop):
    name = obs.obs_property_name(prop)  #"test_<sid>_down" / "test_<sid>_up"
    _, sid, which = name.split("_", 2)
    src = find_source(sid)
    if not src:
        return False
    if which == "down":
        start_fade_for(sid, float(src["min_db"]))
    else:
        start_fade_for(sid, float(src["max_db"]))
    return False  #no need to rebuild UI

def on_name_modified(props, prop, settings):
    #Rename immediately + relabel hotkeys + refresh group label
    pname = obs.obs_property_name(prop)  #"src_<sid]_name"
    sid = pname.split("src_", 1)[1].rsplit("_name", 1)[0]
    new_name = obs.obs_data_get_string(settings, pname).strip()

    s = find_source(sid)
    if s and new_name:
        if s["name"] != new_name:
            s["name"] = new_name
            relabel_hotkeys_for_source(sid, new_name)
            persist_sources_and_refresh()
            return True  #rebuild so group label updates
    return False

def on_number_modified(props, prop, settings):
    #Update numeric fields live and keep min/max sane
    key = obs.obs_property_name(prop)
    try:
        sid = key.split("_", 1)[1].split("_", 1)[0]  #after first "_" get sid
    except Exception:
        return False

    s = find_source(sid)
    if not s:
        return False

    if key.startswith("fade_"):
        s["fade_duration"] = float(obs.obs_data_get_double(settings, key))
    elif key.startswith("min_"):
        s["min_db"] = float(obs.obs_data_get_double(settings, key))
    elif key.startswith("max_"):
        s["max_db"] = float(obs.obs_data_get_double(settings, key))

    #Keep bounds sane
    if s["min_db"] > s["max_db"]:
        s["min_db"], s["max_db"] = s["max_db"], s["min_db"]

    persist_sources_and_refresh()
    return False

#-----------------------------
#Settings lifecycle
#-----------------------------
def script_defaults(settings):
    obs.obs_data_set_default_bool(settings, "show_manager", DEFAULT_SHOW_MANAGER)

    if not sources:
        sid = unique_id()
        sources.append({
            "id": sid,
            "name": "ALL_SOUNDS",
            "fade_duration": DEFAULT_FADE_DURATION,
            "min_db": DEFAULT_MIN_DB,
            "max_db": DEFAULT_MAX_DB,
        })

    obs.obs_data_set_default_string(settings, "sources_json", serialize_sources())
    obs.obs_data_set_default_int(settings, "ui_nonce", 0)

def script_update(settings):
    global SHOW_MANAGER, _g_settings
    _g_settings = settings

    SHOW_MANAGER = obs.obs_data_get_bool(settings, "show_manager")

    saved_json = obs.obs_data_get_string(settings, "sources_json")
    if saved_json and not sources:
        loaded = deserialize_sources(saved_json)
        if loaded:
            sources.clear()
            sources.extend(loaded)

    #Pull values from settings into our model (just in case)
    for s in sources:
        sid = s["id"]
        name_key = f"src_{sid}_name"
        if obs.obs_data_has_user_value(settings, name_key):
            new_name = obs.obs_data_get_string(settings, name_key).strip()
            if new_name and new_name != s["name"]:
                s["name"] = new_name
                relabel_hotkeys_for_source(sid, new_name)

        fd_key = f"fade_{sid}_duration"
        if obs.obs_data_has_user_value(settings, fd_key):
            s["fade_duration"] = float(obs.obs_data_get_double(settings, fd_key))

        min_key = f"min_{sid}_db"
        if obs.obs_data_has_user_value(settings, min_key):
            s["min_db"] = float(obs.obs_data_get_double(settings, min_key))

        max_key = f"max_{sid}_db"
        if obs.obs_data_has_user_value(settings, max_key):
            s["max_db"] = float(obs.obs_data_get_double(settings, max_key))

        if s["min_db"] > s["max_db"]:
            s["min_db"], s["max_db"] = s["max_db"], s["min_db"]

    #Keep serialized copy in settings
    obs.obs_data_set_string(settings, "sources_json", serialize_sources())

def script_save(settings):
    obs.obs_data_set_string(settings, "sources_json", serialize_sources())
    for s in sources:
        sid = s["id"]
        ids = hotkey_ids.get(sid)
        if not ids:
            continue
        arr_down = obs.obs_hotkey_save(ids["down"])
        arr_up = obs.obs_hotkey_save(ids["up"])
        obs.obs_data_set_array(settings, hotkey_storage_key(sid, up=False), arr_down)
        obs.obs_data_set_array(settings, hotkey_storage_key(sid, up=True), arr_up)
        obs.obs_data_array_release(arr_down)
        obs.obs_data_array_release(arr_up)

def script_load(settings):
    global _g_settings
    _g_settings = settings

    saved_json = obs.obs_data_get_string(settings, "sources_json")
    loaded = deserialize_sources(saved_json) if saved_json else []
    if loaded:
        sources.clear()
        sources.extend(loaded)

    for s in sources:
        register_hotkeys_for_source(s["id"], settings=settings)

def script_unload():
    if active_fades:
        obs.timer_remove(update_fades_timer)
    active_fades.clear()

