sys.path.append(RPR_GetResourcePath() + "/Scripts/ReaTeam Extensions/API")

import imgui
import ctypes
import time

#from reaper_python import RPR_OnPlayButton, RPR_OnStopButton, RPR_GetPlayState, RPR_MIDIEditor_GetTake, RPR_MIDIEditor_GetActive, RPR_MIDI_InsertNote, RPR_MIDI_Sort, RPR_MIDI_CountEvts, RPR_MIDI_GetNote, RPR_MIDI_DeleteNote

# WinAPI клавиши
GetAsyncKeyState = ctypes.windll.user32.GetAsyncKeyState
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
VK_BACK = 0x08
VK_SPACE = 0x20
VK_MINUS = 0xBD
VK_EQUAL = 0xBB
VK_LBRACKET = 0xDB
VK_RBRACKET = 0xDD
KEY_0 = 0x30
KEY_9 = 0x39
KEY_R = 0x52
VK_R = 0x52

ctx = None
font = None

NUM_STRINGS = 6
TAB_COLUMNS = 256

CELL_WIDTH = 20
CELL_HEIGHT = 20
START_X = 20
START_Y = 40

cursor_string = 0
cursor_column = 0
key_state = {}
note_durations = {} 
tab_data = [["" for _ in range(TAB_COLUMNS)] for _ in range(NUM_STRINGS)]
digit_buffer = {}  # теперь хранит {(string, column): "12"}

string_tunings = [64, 59, 55, 50, 45, 40]  # Стандартный строй (1-6 струна)
note_lengths = [1920, 960, 480, 240, 120]
note_length_index = 3
use_triplet = False
use_dot = False

note_map = {}  # (string, column) -> pitch
note_cache = set()  # {(startppq, pitch)}


def get_current_note_length():
    base = note_lengths[note_length_index]
    if use_triplet:
        base = int(base * 2 / 3)
    if use_dot:
        base = int(base * 1.5)
    return base


def toggle_playback():
    if RPR_GetPlayState() & 1:
        RPR_OnStopButton()
    else:
        RPR_OnPlayButton()


def is_key_down(vk):
    return (GetAsyncKeyState(vk) & 0x8000) != 0


def insert_note_into_midi(pitch, step_index):
    take = RPR_MIDIEditor_GetTake(RPR_MIDIEditor_GetActive())
    if not take:
        return
    duration = get_current_note_length()
    ppq_start = int(step_index * get_current_note_length())

    ppq_end = ppq_start + duration
    RPR_MIDI_InsertNote(take, True, False, ppq_start, ppq_end, 0, pitch, 100, False)
    RPR_MIDI_Sort(take)

    # сохранить длительность
    for s in range(NUM_STRINGS):
        if string_tunings[s] + int(tab_data[s][step_index]) == pitch:
            note_durations[(s, step_index)] = duration


def delete_note_from_midi(pitch, step_index):
    take = RPR_MIDIEditor_GetTake(RPR_MIDIEditor_GetActive())
    if not take:
        return
    #ppq_start = step_index * 240
    ppq_start = int(step_index * get_current_note_length())

    _, _, note_count, _, _ = RPR_MIDI_CountEvts(take, 0, 0, 0)
    for i in range(note_count):
        note_info = RPR_MIDI_GetNote(take, i, 0, 0, 0, 0, 0, 0, 0)
        ret = note_info[0]
        startppq = note_info[5]
        p = int(note_info[8])
        if ret and p == pitch and startppq == ppq_start:
            RPR_MIDI_DeleteNote(take, i)
            break
    RPR_MIDI_Sort(take)


def draw_duration_info():
    label = ["1", "1/2", "1/4", "1/8", "1/16"][note_length_index]
    if use_triplet:
        label += " triplet"
    if use_dot:
        label += "."
    imgui.DrawList_AddText(imgui.GetForegroundDrawList(ctx), 20, 10, 0xFFFFFFFF, f"♪ {label}")


def import_notes_from_midi():
    global tab_data, note_map, note_durations, note_cache
    #tab_data = [["" for _ in range(TAB_COLUMNS)] for _ in range(NUM_STRINGS)]
    #note_map.clear()
    #note_durations.clear()
    note_cache.clear()

    editor = RPR_MIDIEditor_GetActive()
    take = RPR_MIDIEditor_GetTake(editor)
    if not take:
        return

    _, _, note_count, _, _ = RPR_MIDI_CountEvts(take, 0, 0, 0)
    for i in range(note_count):
        note_info = RPR_MIDI_GetNote(take, i, 0, 0, 0, 0, 0, 0, 0)
        retval = note_info[0]
        startppq = note_info[5]
        endppq = note_info[6]
        pitch = int(note_info[8])

        if not retval:
            continue

        step = round(startppq / get_current_note_length()) 
        note_cache.add((step, pitch))

        duration = endppq - startppq

        preferred = None
        candidates = []

        for s, base in enumerate(string_tunings):
            fret = pitch - base
            if 0 <= fret < 100 and step < TAB_COLUMNS:
                key = (s, step)
                if key not in note_map:
                    candidates.append((s, fret, key))
                if tab_data[s][step] == str(fret):
                    preferred = (s, fret, key)

        if preferred:
            s, fret, key = preferred
        elif candidates:
            s, fret, key = candidates[0]
        else:
            continue

        tab_data[s][step] = str(fret)
        note_map[key] = pitch
        note_durations[key] = duration
    
    # удаляем устаревшие ноты (если удалены в DAW)
    for key in list(note_map.keys()):
        s, c = key
        pitch = note_map[key]
        if (c, pitch) not in note_cache:
            tab_data[s][c] = ""
            del note_map[key]
            if key in note_durations:
                del note_durations[key]


def check_midi_changes():
    take = RPR_MIDIEditor_GetTake(RPR_MIDIEditor_GetActive())
    if not take:
        return

    _, _, note_count, _, _ = RPR_MIDI_CountEvts(take, 0, 0, 0)
    current = set()

    for i in range(note_count):
        note_info = RPR_MIDI_GetNote(take, i, 0, 0, 0, 0, 0, 0, 0)
        if note_info[0]:
            startppq = note_info[5]
            pitch = int(note_info[8])
            step = round(startppq / get_current_note_length()) 
            current.add((step, pitch))

    global note_cache
    if current != note_cache:
        import_notes_from_midi()
        
def clear_console():
    RPR_ShowConsoleMsg("\x0c")

def format_note_string(s, c):
    val = tab_data[s][c]
    if val in ("", "-"):
        return val

    key = (s, c)
    dur = note_durations.get(key, get_current_note_length())
    suffix = ""

    if dur == 360:
        suffix = "*"
    elif dur == 480:
        suffix = "."
    elif dur == 720:
        suffix = ".."
    elif dur == 960:
        suffix = "..."

    # Строгая проверка на триоль (2/3 от базовой длительности)
    # Проверка на триоль
    if dur in (160, 320, 640, 1280):
        suffix += "t"


    return str(val) + suffix

def loop():
    global cursor_string, cursor_column, key_state, note_length_index, use_triplet, use_dot

    imgui.SetNextWindowSize(ctx, 1300, 240, imgui.Cond_FirstUseEver())
    visible, open = imgui.Begin(ctx, "Tab Editor", True)
    
    info = RPR_TimeMap_GetMeasureInfo(0, 0, 0.0, 0.0, 0, 0, 0.0)
    #RPR_ShowConsoleMsg(f"MEASURE 0: {info}\n")
    

    if visible:
        check_midi_changes()
        imgui.PushFont(ctx, font)
        draw_list = imgui.GetWindowDrawList(ctx)
        x = START_X + int(imgui.GetWindowPos(ctx)[0])
        y = START_Y + int(imgui.GetWindowPos(ctx)[1])

        # отлавливаем клик мыши для назначения курсора на него
        mouse_x, mouse_y = imgui.GetMousePos(ctx)
        if imgui.IsMouseClicked(ctx, 0):
            local_x = mouse_x - x
            local_y = mouse_y - y
            clicked_col = int(local_x // CELL_WIDTH)
            clicked_string = int(local_y // CELL_HEIGHT)
            if 0 <= clicked_col < TAB_COLUMNS and 0 <= clicked_string < NUM_STRINGS:
                cursor_column = clicked_col
                cursor_string = clicked_string

        # линии струн
        for string in range(NUM_STRINGS):
            y_pos = y + string * CELL_HEIGHT
            imgui.DrawList_AddLine(draw_list, x, y_pos, x + TAB_COLUMNS * CELL_WIDTH, y_pos, 0xFFFFFFFF, 1.0)

        # ноты
 

        for string in range(NUM_STRINGS):
            y_pos = y + string * CELL_HEIGHT
            for col in range(TAB_COLUMNS):
                note = format_note_string(string, col)
                if note:
                    imgui.DrawList_AddText(draw_list, x + col * CELL_WIDTH + 2, y_pos + 2, 0xFFFFFFFF, str(note))

        
                # тактовые черты из DAW (адаптивно к сетке и позиции)
        proj = 0
        qn_start_view = RPR_TimeMap2_timeToQN(proj, RPR_GetCursorPosition())
        qn_per_column = get_current_note_length() / 960
        qn_end_view = qn_start_view + TAB_COLUMNS * qn_per_column

        measure = 0
        while True:
            info = RPR_TimeMap_GetMeasureInfo(proj, measure, 0.0, 0.0, 0, 0, 0.0)
            qn_start = info[3]
            qn_end = info[4]
            if qn_start > qn_end_view:
                break
            if qn_end >= qn_start_view:
                col = int((qn_start - qn_start_view) / qn_per_column)
                #RPR_ShowConsoleMsg(f"[MEASURE {measure}] QN_START: {qn_start:.2f}, COL: {col}\n")
                if 0 <= col < TAB_COLUMNS:
                    bar_x = x + col * CELL_WIDTH
                    imgui.DrawList_AddLine(draw_list, bar_x, y, bar_x, y + (NUM_STRINGS - 1) * CELL_HEIGHT, 0xFF888888, 1.0)
            measure += 1

        # DAW курсор (зелёный)
        play_pos_qn = RPR_TimeMap2_timeToQN(0, RPR_GetPlayPosition())
        col_pos = int((play_pos_qn - qn_start_view) / qn_per_column)
        #RPR_ShowConsoleMsg(f"[DAW CURSOR] play_qn: {play_pos_qn:.2f}, col_pos: {col_pos}\n")
        if 0 <= col_pos < TAB_COLUMNS:
            px = x + col_pos * CELL_WIDTH
            imgui.DrawList_AddLine(draw_list, px + CELL_WIDTH // 2, y - 10, px + CELL_WIDTH // 2, y, 0x00FF00FF, 2.0)

        # курсор таба (красный)
        cx = x + cursor_column * CELL_WIDTH
        cy = y + cursor_string * CELL_HEIGHT
        imgui.DrawList_AddRect(draw_list, cx, cy, cx + CELL_WIDTH, cy + CELL_HEIGHT, 0xFF0000FF, 0.0, 0, 2.0)

        if 0 <= col_pos < TAB_COLUMNS:
            px = x + col_pos * CELL_WIDTH
            imgui.DrawList_AddLine(draw_list, px + CELL_WIDTH // 2, y - 10, px + CELL_WIDTH // 2, y + NUM_STRINGS * CELL_HEIGHT, 0xFF00FF00, 2.0)
            
        window_focused = imgui.IsWindowFocused(ctx)

        if window_focused:
            
            now = time.time()
            
        # обычные стрелки (с автоповтором)
            for key, direction in [(VK_LEFT, -1), (VK_RIGHT, 1), (VK_UP, -1), (VK_DOWN, 1)]:
                key_name = f"repeat_{key}"
                axis = "horizontal" if key in (VK_LEFT, VK_RIGHT) else "vertical"

                state = key_state.setdefault(key_name, {"active": False, "pressed_at": 0, "repeat_timer": 0})
                if is_key_down(key):
                    now = time.time()
                    if not state["active"]:
                        state["active"] = True
                        state["pressed_at"] = now
                        state["repeat_timer"] = now + 0.4

                        # первый шаг
                        if axis == "horizontal":
                            cursor_column = max(0, min(TAB_COLUMNS - 1, cursor_column + direction))
                        else:
                            cursor_string = max(0, min(NUM_STRINGS - 1, cursor_string + direction))

                    elif now >= state["repeat_timer"]:
                        state["repeat_timer"] = now + 0.07
                        if axis == "horizontal":
                            cursor_column = max(0, min(TAB_COLUMNS - 1, cursor_column + direction))
                        else:
                            cursor_string = max(0, min(NUM_STRINGS - 1, cursor_string + direction))
                else:
                    key_state[key_name] = {"active": False, "pressed_at": 0, "repeat_timer": 0}

            #if is_key_down(VK_UP) and not key_state.get(VK_UP, False):
            #    cursor_string = max(0, cursor_string - 1)
            #if is_key_down(VK_DOWN) and not key_state.get(VK_DOWN, False):
            #    cursor_string = min(NUM_STRINGS - 1, cursor_string + 1)

            # перемещение по целым тактам по ctrl+стрелка
            ctrl_combo = imgui.IsKeyDown(ctx, imgui.Mod_Ctrl())

            # ctrl + LEFT
            if is_key_down(VK_LEFT) and ctrl_combo:
                state = key_state.setdefault("ctrl_left_jump", {"active": False, "pressed_at": 0, "repeat_timer": 0})
                if not state["active"]:
                    state["active"] = True
                    state["pressed_at"] = now
                    state["repeat_timer"] = now + 0.4
                    # шаг ←
                    qn_per_column = get_current_note_length() / 960
                    columns_per_measure = int(4.0 / qn_per_column)
                    if cursor_column % columns_per_measure == 0:
                        cursor_column = max(0, cursor_column - columns_per_measure)
                    else:
                        cursor_column = cursor_column - (cursor_column % columns_per_measure)
                elif now >= state["repeat_timer"]:
                    state["repeat_timer"] = now + 0.07
                    qn_per_column = get_current_note_length() / 960
                    columns_per_measure = int(4.0 / qn_per_column)
                    if cursor_column % columns_per_measure == 0:
                        cursor_column = max(0, cursor_column - columns_per_measure)
                    else:
                        cursor_column = cursor_column - (cursor_column % columns_per_measure)
            else:
                key_state["ctrl_left_jump"] = {"active": False, "pressed_at": 0, "repeat_timer": 0}

            # ctrl + RIGHT
            if is_key_down(VK_RIGHT) and ctrl_combo:
                state = key_state.setdefault("ctrl_right_jump", {"active": False, "pressed_at": 0, "repeat_timer": 0})
                if not state["active"]:
                    state["active"] = True
                    state["pressed_at"] = now
                    state["repeat_timer"] = now + 0.4
                    # шаг →
                    qn_per_column = get_current_note_length() / 960
                    columns_per_measure = int(4.0 / qn_per_column)
                    cursor_column = min(TAB_COLUMNS - 1, cursor_column + columns_per_measure - (cursor_column % columns_per_measure))
                elif now >= state["repeat_timer"]:
                    state["repeat_timer"] = now + 0.07
                    qn_per_column = get_current_note_length() / 960
                    columns_per_measure = int(4.0 / qn_per_column)
                    cursor_column = min(TAB_COLUMNS - 1, cursor_column + columns_per_measure - (cursor_column % columns_per_measure))
            else:
                key_state["ctrl_right_jump"] = {"active": False, "pressed_at": 0, "repeat_timer": 0}
            

            key = (cursor_string, cursor_column)

            for vk in range(KEY_0, KEY_9 + 1):
                if is_key_down(vk) and not key_state.get(vk, False):
                    digit = chr(vk)
                    prev_buffer = digit_buffer.get(key, "")

                    # если длина >= 2 или результат > 29 — сбрасываем буфер
                    new_buffer = prev_buffer + digit
                    try:
                        fret_val = int(new_buffer)
                        if len(new_buffer) > 2 or fret_val > 29:
                            new_buffer = digit
                    except:
                        new_buffer = digit

                    # удалить старую ноту перед вставкой новой
                    old_pitch = note_map.get(key)
                    if old_pitch is not None:
                        delete_note_from_midi(old_pitch, cursor_column)
                        del note_map[key]

                    digit_buffer[key] = new_buffer
                    tab_data[cursor_string][cursor_column] = new_buffer

                    try:
                        pitch = string_tunings[cursor_string] + int(new_buffer)
                        note_map[key] = pitch
                        insert_note_into_midi(pitch, cursor_column)
                    except:
                        pass

                    break

            if is_key_down(KEY_R) and not key_state.get(KEY_R, False):
                tab_data[cursor_string][cursor_column] = "-"
                digit_buffer.pop(key, None)

            if is_key_down(VK_BACK) and not key_state.get(VK_BACK, False):
                tab_data[cursor_string][cursor_column] = ""
                pitch = note_map.get(key)
                if pitch is not None:
                    delete_note_from_midi(pitch, cursor_column)
                    del note_map[key]
                digit_buffer.pop(key, None)

            if is_key_down(VK_SPACE) and not key_state.get(VK_SPACE, False):
                toggle_playback()

            
            if any(is_key_down(k) and not key_state.get(k, False) for k in [VK_MINUS, VK_EQUAL, VK_LBRACKET, VK_RBRACKET]):
                # для смены масштабирования и размера запонимаем позицию курсора, чтоб не уехала
                ppq_pos = int(cursor_column * get_current_note_length())

                if is_key_down(VK_MINUS) and not key_state.get(VK_MINUS, False):
                    note_length_index = max(0, note_length_index - 1)
                if is_key_down(VK_EQUAL) and not key_state.get(VK_EQUAL, False):
                    note_length_index = min(len(note_lengths) - 1, note_length_index + 1)
                if is_key_down(VK_LBRACKET) and not key_state.get(VK_LBRACKET, False):
                    use_triplet = not use_triplet
                if is_key_down(VK_RBRACKET) and not key_state.get(VK_RBRACKET, False):
                    use_dot = not use_dot
                
                # возвращаем курсор на место
                cursor_column = int(ppq_pos / get_current_note_length())

            for vk in [VK_BACK, VK_SPACE, VK_R, VK_MINUS, VK_EQUAL, VK_LBRACKET, VK_RBRACKET] + list(range(KEY_0, KEY_9 + 1)):
                key_state[vk] = is_key_down(vk)

        draw_duration_info()
        imgui.PopFont(ctx)
        imgui.End(ctx)

    if open:
        RPR_defer("loop()")


def init():
    global ctx, font
    ctx = imgui.CreateContext("Tab Editor")
    font = imgui.CreateFont("Courier New", 16)
    imgui.Attach(ctx, font)
    #clear_console()
    import_notes_from_midi()
    RPR_defer("loop()")

  
RPR_defer("init()")
