# 표준 라이브러리
import time
import tkinter as tk
from tkinter import scrolledtext, messagebox, StringVar, simpledialog
import os
import sys
import threading

# 서드파티 라이브러리
import serial
import serial.tools.list_ports
import win32pipe, win32file, pywintypes

# ------------------------------
# Global Variables
# ------------------------------
arduino = None
multimeter = None
multimeter2 = None
power_supply = None
Nordic_mcu = None
last_script_context = None
I2C_ADDR = 0x39

# --- Search Results ---
best_bgr_code = -1
best_mclk_code = -1
best_wclk_code = -1
best_led1_code = -1
best_led2_code = -1
best_tc_code = -1

# --- GUI Widget Variables (initialized to None to solve Pylance errors) ---
root = None
log_text = None
console_text_input = None
entry_i2c_reg = None
entry_i2c_data = None
entry_pulse_count = None
entry_sample_num = None
entry_sample_num_m = None
entry_sample_num_d = None

# Result StringVars
bgr_result_var = None
mclk_result_var = None
wclk_result_var = None
led1_result_var = None
led2_result_var = None
tc_min_result_var = None

# Trim Code StringVars
bgr_trim_code_var = None
mclk_trim_code_var = None
wclk_trim_code_var = None
led1_trim_code_var = None
led2_trim_code_var = None
tc_trim_code_var = None
current_measurement_context = None

# Button Widgets
bgr_search_button = None
mclk_search_button = None
wclk_search_button = None
led1_search_button = None
led2_search_button = None
tc_search_button = None
tc_search_m2_button = None
run_all_searches_button = None # New global variable for the "Run All" button
run_all_searches_button2 = None

# COM Port StringVars
com_arduino_var = None
com_dmm_var = None
com_dmm2_var = None
com_psu_var = None

# ------------------------------
# Named Pipe Server for Nordic_mcu
# ------------------------------
PIPE_SERVER_STOC = r'\\.\pipe\pipeServer_StoC'
PIPE_SERVER_CTOS = r'\\.\pipe\PipeServer_CtoS'
class PipeServer:
    def __init__(self, master):
        self.master = master
        self.pipeServer_StoC = None
        self.pipeServer_CtoS = None
        self.running = False 
        self.last_response = ""
        self.result_ready = threading.Event()

    def connect_pipe_server(self):
        try:
            if not self.pipeServer_StoC:
                self.pipeServer_StoC = win32file.CreateFile(
                    PIPE_SERVER_STOC,
                    win32file.GENERIC_READ,
                    0, None, win32file.OPEN_EXISTING, 0, None
                )

            if not self.pipeServer_CtoS:
                self.pipeServer_CtoS = win32file.CreateFile(
                    PIPE_SERVER_CTOS,
                    win32file.GENERIC_WRITE,
                    0, None, win32file.OPEN_EXISTING, 0, None
                )

            log_text.insert(tk.END, "[INFO] Connected to Nordic_mcu\n")
            log_text.see(tk.END)

            self.running = True
            threading.Thread(target=self.listen_messages, daemon=True).start()
            return self
        except pywintypes.error as e:
            if e.winerror:
                self.close()
            log_text.insert(tk.END, f"[ERROR] Connect Fail to Nordic_mcu.. {e}\n")
            log_text.see(tk.END)

        return None
    
    def listen_messages(self):
        while self.running:
            try:
                result, data = win32file.ReadFile(self.pipeServer_StoC, 4096, None)
                msg = data.decode('utf-8')

                if msg == "ESC":
                    self.close()
                    break
                else:
                    self.last_response = msg           
                    self.result_ready.set()

                self.master.after(0, lambda m=msg: log_text.insert(tk.END, f"[RECV] {m}\n"))
                
            except pywintypes.error:
                break

    def send_sync(self, msg, timeout=6.0):
        """Sends a message and waits for a response synchronously."""
        try:
            if not self.running or not self.pipeServer_CtoS:
                return "NAK_NOT_CONNECTED"

            self.last_response = ""
            self.result_ready.clear()

            self.master.after(0, lambda: log_text.insert(tk.END, f"[SEND] {msg}\n"))
            win32file.WriteFile(self.pipeServer_CtoS, msg.encode('utf-8'))

            if self.result_ready.wait(timeout): # wait for response or timeout
                return self.last_response
            else:
                self.master.after(0, lambda: log_text.insert(tk.END, f"[ERROR] Timeout waiting for response to: {msg}\n"))
                return "NAK_TIMEOUT"

        except Exception as e:
            self.master.after(0, lambda: log_text.insert(tk.END, f"[ERROR] send_sync error: {e}\n"))
            return "NAK_EXCEPTION"

    def wait_response(self, timeout=6.0):
        """Waits for the next response without sending a command."""
        self.result_ready.clear()
        if self.result_ready.wait(timeout):
            return self.last_response
        self.master.after(0, lambda: log_text.insert(tk.END, "[ERROR] Timeout waiting for I2C read data.\n"))
        return "NAK_TIMEOUT"

    def send_message(self, message):
        if self.running and self.pipeServer_CtoS:
            try:
                win32file.WriteFile(self.pipeServer_CtoS, message.encode('utf-8'))
                self.master.after(0, lambda: log_text.insert(tk.END, f"[SEND] {message}\n"))
            except Exception as e:
                self.master.after(0, lambda: log_text.insert(tk.END, f"[ERROR] send_message error: {e}\n"))

    def close(self):
        self.running = False
        self.result_ready.set() # Unblock any waiting threads
        if self.pipeServer_CtoS:
            win32file.CloseHandle(self.pipeServer_CtoS)
            self.pipeServer_CtoS = None
        if self.pipeServer_StoC:
            win32file.CloseHandle(self.pipeServer_StoC)
            self.pipeServer_StoC = None
        
        self.master.after(0, lambda: log_text.insert(tk.END, "[INFO] Disconnected from Nordic_mcu\n"))

# ------------------------------
# Multimeter Class (Refactored)
# ------------------------------
class MultimeterInstrument:
    def __init__(self, name, log_prefix):
        self.name = name
        self.log_prefix = log_prefix
        self.ser = None

    def connect(self, port):
        try:
            self.ser = serial.Serial(port, 9600, timeout=1)
            log_text.insert(tk.END, f"[INFO] {self.name} connected on {port}\n")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None
            log_text.insert(tk.END, f"[INFO] {self.name} disconnected\n")

    def write(self, cmd):
        if self.ser:
            if isinstance(cmd, str): cmd = cmd.encode()
            self.ser.write(cmd)

    def read_stripped(self):
        if self.ser:
            return self.ser.readline().decode().strip()
        return None

    def configure_voltage(self):
        if not self.ser: return
        self.write(b'*CLS\n'); time.sleep(0.05)
        self.write(b'CONF:VOLT:DC 10\n'); time.sleep(0.05)
        self.write(b'VOLT:DC:RANG 10\n'); time.sleep(0.05)
        self.write(b'VOLT:DC:NPLC 1\n'); time.sleep(0.05)
        self.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)

    def configure_freq(self):
        if not self.ser: return
        self.write(b'*CLS\n'); time.sleep(0.05)
        self.write(b'CONF:FREQ\n'); time.sleep(0.05)
        self.write(b'FREQ:APER 0.1\n'); time.sleep(0.05)
        self.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)

    def configure_current(self):
        if not self.ser: return
        self.write(b'*CLS\n'); time.sleep(0.05)
        self.write(b'CONF:CURR:DC\n'); time.sleep(0.05)
        self.write(b'CURR:DC:NPLC 1\n'); time.sleep(0.05)
        self.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)

    def get_raw_measurement(self, config_func, wait_init=0.2, wait_fetc=0.55):
        if not self.ser: return None
        config_func()
        self.write(b'INIT\n'); time.sleep(wait_init)
        self.write(b'FETC?\n'); time.sleep(wait_fetc)
        val = self.read_stripped()
        self.write(b'*CLS\n')
        return val

    def get_fast_measurement(self, wait_init=0.2, wait_fetc=0.2):
        if not self.ser: return None
        self.write(b'INIT\n'); time.sleep(wait_init)
        self.write(b'FETC?\n'); time.sleep(wait_fetc)
        return self.read_stripped()

# ------------------------------
# I2C Control Functions
# ------------------------------
def i2c_write(reg, data):
    """Sends an I2C write command via the Nordic_mcu or Arduino."""
    # Prioritize Nordic_mcu
    if Nordic_mcu and Nordic_mcu.running:
        cmd = f"W,0x{reg:02X},0x{data:02X}"
        return Nordic_mcu.send_sync(cmd)
    # Fallback to Arduino
    elif arduino:
        arduino.reset_input_buffer()
        cmd = f"W,{I2C_ADDR:#04x},{reg:#04x},{data:#04x}\n"
        arduino.write(cmd.encode())
        return arduino.readline().decode().strip()
    return "No controller connected"

def i2c_read(reg):
    """Sends an I2C read command via the Nordic_mcu or Arduino."""
    # Prioritize Nordic_mcu
    if Nordic_mcu and Nordic_mcu.running:
        cmd = f"R,0x{reg:02X}"
        resp = Nordic_mcu.send_sync(cmd)
        if not resp or "NAK" in resp: return None
        if resp == "ACK":  # MCU가 ACK 후 DATA를 별도로 보내는 경우
            resp = Nordic_mcu.wait_response()
            if not resp or "NAK" in resp: return None
        try:
            if resp.startswith("DATA"):
                _, val_str = resp.split(",")
                return int(val_str, 16)
            return int(resp, 16)
        except (ValueError, IndexError):
            root.after(0, lambda: log_text.insert(tk.END, f"[ERROR] Could not parse I2C read response: '{resp}'\n"))
            return None
    # Fallback to Arduino
    elif arduino:
        arduino.reset_input_buffer()
        cmd = f"R,{I2C_ADDR:#04x},{reg:#04x}\n"
        arduino.write(cmd.encode())
        resp = arduino.readline().decode().strip()
        if resp.startswith("DATA"):
            _, val = resp.split(",")
            return int(val, 16)
        return None
    return None

def send_pulse_command_internal(count):
    """Internal helper to send a pulse command to the active controller."""
    # Prioritize Nordic_mcu
    if Nordic_mcu and Nordic_mcu.running:
        cmd = f"t,{count}"
        # Use sync method. The MCU is expected to send an 'ACK' after processing the pulse command.
        response = Nordic_mcu.send_sync(cmd)
        if response and "ACK" in response:
            return True
        else:
            # Log the failure to get a proper ACK to help with debugging
            root.after(0, lambda: log_text.insert(tk.END, f"[ERROR] Pulse command (t,{count}) was not acknowledged by MCU. Response: {response}\n"))
            return False
    # Fallback to Arduino
    elif arduino:
        cmd = f"t,{count}\n"
        arduino.write(cmd.encode())
        return True
    return False

# ------------------------------
# BGR Trim Code Calculation
# ------------------------------
def calculate_bgr_trim_code(measured_voltage):
    """Calculates the BGR trim code and operation based on the measured voltage."""
    try:
        delta = 1.115 - float(measured_voltage)

        if delta < -0.025:
            return "- [0001000]"
        elif -0.025 <= delta < -0.022:
            return "- [0000111]"
        elif -0.022 <= delta < -0.019:
            return "- [0000110]"
        elif -0.019 <= delta < -0.016:
            return "- [0000101]"
        elif -0.016 <= delta < -0.013:
            return "- [0000100]"
        elif -0.013 <= delta < -0.010:
            return "- [0000011]"
        elif -0.010 <= delta < -0.007:
            return "- [0000010]"
        elif -0.007 <= delta < -0.004:
            return "- [0000001]"
        elif -0.004 <= delta < 0.004:
            return "No Trim"
        elif 0.004 <= delta < 0.007:
            return "+ [0000001]"
        elif 0.007 <= delta < 0.010:
            return "+ [0000010]"
        elif 0.010 <= delta < 0.013:
            return "+ [0000011]"
        elif 0.013 <= delta < 0.016:
            return "+ [0000100]"
        elif 0.016 <= delta < 0.019:
            return "+ [0000101]"
        elif 0.019 <= delta < 0.022:
            return "+ [0000110]"
        elif 0.022 <= delta < 0.025:
            return "+ [0000111]"
        elif 0.025 <= delta:
            return "+ [0001000]"
    except (ValueError, TypeError):
        return "Calc Err"

# ------------------------------
# Result Display Logic
# ------------------------------
def update_result_display(raw_value, unit, context_hint=None):
    """Parses the last value from the instrument, formats it, and displays it."""
    global last_script_context, current_measurement_context
    context = context_hint or current_measurement_context or last_script_context
    used_temp_context = context_hint is None and current_measurement_context and context == current_measurement_context
    if not context: # Do nothing if no TRIM script was run
        return

    try:
        if ',' in raw_value:
            last_value_str = raw_value.split(',')[-1]
        else:
            last_value_str = raw_value
        
        numeric_value = float(last_value_str)
        
        display_unit = unit
        if context in ["wclk", "mclk"] and unit == "Hz":
            numeric_value /= 1000
            display_unit = "kHz"
        elif context in ["led1", "led2", "tc"] and unit == "A":
            numeric_value *= 1000
            display_unit = "mA"

        formatted_result = f"{numeric_value:.3f} {display_unit}"
        
        if context == "bgr":
            bgr_result_var.set(formatted_result)
            # Only calculate trim code for voltage measurements
            if unit == "V":
                trim_code = calculate_bgr_trim_code(numeric_value)
                bgr_trim_code_var.set(trim_code)
        elif context == "wclk":
            wclk_result_var.set(formatted_result)
        elif context == "mclk":
            mclk_result_var.set(formatted_result)
        elif context == "led1":
            led1_result_var.set(formatted_result)
        elif context == "led2":
            led2_result_var.set(formatted_result)
        elif context == "tc":
            tc_min_result_var.set(formatted_result)
            
    except (ValueError, IndexError):
        error_msg = "Parse Err"
        if context == "bgr":
            bgr_result_var.set(error_msg)
            bgr_trim_code_var.set("Error")
        elif context == "wclk":
            wclk_result_var.set(error_msg)
        elif context == "mclk":
            mclk_result_var.set(error_msg)
        elif context == "led1":
            led1_result_var.set(error_msg)
        elif context == "led2":
            led2_result_var.set(error_msg)
        elif context == "tc":
            tc_min_result_var.set(error_msg)
    finally:
        if used_temp_context:
            current_measurement_context = None


# ------------------------------
# Multimeter Functions
# ------------------------------

# --- Multimeter 1 ---

def _get_raw_voltage():
    """Internal function to get a raw voltage reading. Returns raw string or None."""
    if not multimeter: return None
    ser = multimeter
    ser.write(b'*CLS\n'); time.sleep(0.05)
    ser.write(b'CONF:VOLT:DC 10\n'); time.sleep(0.05)
    ser.write(b'VOLT:DC:RANG 10\n'); time.sleep(0.05)
    ser.write(b'VOLT:DC:NPLC 1\n'); time.sleep(0.05)
    ser.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)
    ser.write(b'INIT\n'); time.sleep(0.2)
    ser.write(b'FETC?\n'); time.sleep(0.55)
    value = ser.readline().decode().strip()
    ser.write(b'*CLS\n')
    return value

def _get_fast_voltage():
    """Internal function for fast voltage reading, assuming DMM is already configured."""
    if not multimeter: return None
    ser = multimeter
    ser.write(b'INIT\n'); time.sleep(0.2)
    ser.write(b'FETC?\n'); time.sleep(0.2)
    return ser.readline().decode().strip()

def measure_voltage():
    if not multimeter:
        log_text.insert(tk.END, "[ERROR] Multimeter not connected\n")
        return
    raw_value = multimeter.get_raw_measurement(multimeter.configure_voltage)
    if raw_value is None:
        return
    log_text.insert(tk.END, f"[DMM] Voltage = {raw_value} V\n")
    log_text.see(tk.END)
    update_result_display(raw_value, "V")

def _get_fast_freq():
    """Internal function for fast frequency reading, assuming DMM is already configured."""
    if not multimeter: return None
    ser = multimeter
    ser.write(b'INIT\n'); time.sleep(0.4)
    ser.write(b'FETC?\n'); time.sleep(0.4)
    return ser.readline().decode().strip()

def measure_freq():
    if not multimeter:
        log_text.insert(tk.END, "[ERROR] Multimeter not connected\n")
        return
    # Freq measurement needs slightly different wait times
    val = multimeter.get_raw_measurement(multimeter.configure_freq, wait_init=0.55, wait_fetc=0.55)
    if val:
        log_text.insert(tk.END, f"[DMM] Frequency = {val} Hz\n")
        log_text.see(tk.END)
        update_result_display(val, "Hz")

def _get_fast_current():
    """Internal function for fast current reading, assuming DMM is already configured."""
    if not multimeter: return None
    ser = multimeter
    ser.write(b'INIT\n'); time.sleep(0.2)
    ser.write(b'FETC?\n'); time.sleep(0.7)
    return ser.readline().decode().strip()

def measure_current():
    if not multimeter:
        log_text.insert(tk.END, "[ERROR] Multimeter not connected\n")
        return
    val = multimeter.get_raw_measurement(multimeter.configure_current)
    if val:
        log_text.insert(tk.END, f"[DMM] Current = {val} A\n")
        log_text.see(tk.END)
        update_result_display(val, "A")

# --- Multimeter 2 ---

def _get_raw_voltage2():
    """Internal function to get a raw voltage reading from DMM2."""
    if not multimeter2: return None
    ser = multimeter2
    ser.write(b'*CLS\n'); time.sleep(0.05)
    ser.write(b'CONF:VOLT:DC 10\n'); time.sleep(0.05)
    ser.write(b'VOLT:DC:RANG 10\n'); time.sleep(0.05)
    ser.write(b'VOLT:DC:NPLC 1\n'); time.sleep(0.05)
    ser.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)
    ser.write(b'INIT\n'); time.sleep(0.2)
    ser.write(b'FETC?\n'); time.sleep(0.55)
    value = ser.readline().decode().strip()
    ser.write(b'*CLS\n')
    return value

def _get_fast_voltage2():
    """Internal function for fast voltage reading from DMM2, assuming it's configured."""
    if not multimeter2: return None
    ser = multimeter2
    ser.write(b'INIT\n'); time.sleep(0.2)
    ser.write(b'FETC?\n'); time.sleep(0.2)
    return ser.readline().decode().strip()

def measure_voltage2():
    if not multimeter2:
        log_text.insert(tk.END, "[ERROR] Multimeter 2 not connected\n")
        return
    raw_value = multimeter2.get_raw_measurement(multimeter2.configure_voltage)
    if raw_value is None:
        return
    log_text.insert(tk.END, f"[DMM2] Voltage = {raw_value} V\n")
    log_text.see(tk.END)
    update_result_display(raw_value, "V")

def _get_fast_freq2():
    """Internal function for fast frequency reading from DMM2, assuming it's configured."""
    if not multimeter2: return None
    ser = multimeter2
    ser.write(b'INIT\n'); time.sleep(0.4)
    ser.write(b'FETC?\n'); time.sleep(0.4)
    return ser.readline().decode().strip()

def measure_freq2():
    if not multimeter2:
        log_text.insert(tk.END, "[ERROR] Multimeter 2 not connected\n")
        return
    val = multimeter2.get_raw_measurement(multimeter2.configure_freq, wait_init=0.55, wait_fetc=0.55)
    if val:
        log_text.insert(tk.END, f"[DMM2] Frequency = {val} Hz\n")
        log_text.see(tk.END)
        update_result_display(val, "Hz")

def _get_fast_current2():
    """Internal function for fast current reading from DMM2, assuming it's configured."""
    if not multimeter2: return None
    ser = multimeter2
    ser.write(b'INIT\n'); time.sleep(0.2)
    ser.write(b'FETC?\n'); time.sleep(0.7)
    return ser.readline().decode().strip()

def measure_current2():
    if not multimeter2:
        log_text.insert(tk.END, "[ERROR] Multimeter 2 not connected\n")
        return
    val = multimeter2.get_raw_measurement(multimeter2.configure_current)
    if val:
        log_text.insert(tk.END, f"[DMM2] Current = {val} A\n")
        log_text.see(tk.END)
        update_result_display(val, "A")

# ------------------------------
# Port Connection Management
# ------------------------------
def scan_com_ports():
    ports = serial.tools.list_ports.comports()
    return [port.device for port in ports]

def open_arduino():
    global arduino
    try:
        arduino = serial.Serial(com_arduino_var.get(), 115200, timeout=1)
        log_text.insert(tk.END, f"[INFO] Arduino connected on {com_arduino_var.get()}\n")
    except Exception as e:
        messagebox.showerror("Error", str(e))

def close_arduino():
    global arduino
    if arduino:
        arduino.close()
        arduino = None
        log_text.insert(tk.END, "[INFO] Arduino disconnected\n")

def open_multimeter():
    global multimeter
    multimeter = MultimeterInstrument("Multimeter", "DMM")
    multimeter.connect(com_dmm_var.get())

def open_multimeter2():
    global multimeter2
    multimeter2 = MultimeterInstrument("Multimeter 2", "DMM2")
    multimeter2.connect(com_dmm2_var.get())

def close_multimeter():
    global multimeter
    if multimeter:
        multimeter.close()
        multimeter = None
        log_text.insert(tk.END, "[INFO] Multimeter disconnected\n")

def close_multimeter2():
    global multimeter2
    if multimeter2:
        multimeter2.close()
        multimeter2 = None
        log_text.insert(tk.END, "[INFO] Multimeter2 disconnected\n")

def open_psu():
    global power_supply
    try:
        power_supply = serial.Serial(com_psu_var.get(), 9600, timeout=1)
        log_text.insert(tk.END, f"[INFO] Power Supply connected on {com_psu_var.get()}\n")
    except Exception as e:
        messagebox.showerror("Error", str(e))

def close_psu():
    global power_supply
    if power_supply:
        power_supply.close()
        power_supply = None
        log_text.insert(tk.END, "[INFO] Power Supply disconnected\n")

def control_arduino_relay(relay_num, state):
    """Sends a command to control a specific relay on the Arduino."""
    if not arduino or not arduino.is_open:
        messagebox.showerror("Connection Error", "Arduino is not connected.")
        return

    try:
        cmd = f"REL,{relay_num},{state.upper()}\n"
        arduino.write(cmd.encode())
        log_text.insert(tk.END, f"[ARDUINO] Sending: {cmd.strip()}\n")
        
        # Read response from Arduino
        response = arduino.readline().decode().strip()
        if response:
            log_text.insert(tk.END, f"[ARDUINO RSP] {response}\n")
        log_text.see(tk.END)

    except Exception as e:
        messagebox.showerror("Relay Control Error", f"Failed to send command to Arduino: {e}")
        log_text.insert(tk.END, f"[ERROR] Relay control failed: {e}\n")

# ------------------------------
# GUI Button Actions
# ------------------------------
def write_i2c():
    try:
        reg_str = entry_i2c_reg.get()
        data_str = entry_i2c_data.get()
        if not reg_str or not data_str:
            messagebox.showerror("Input Error", "ADDRESS and DATA fields cannot be empty for writing.")
            return
        reg = int(reg_str, 16)
        data = int(data_str, 16)
        resp = i2c_write(reg, data)
        log_text.insert(tk.END, f"[I2C WRITE] Reg=0x{reg:02X}, Data=0x{data:02X} -> {resp}\n")
        log_text.see(tk.END)
    except ValueError:
        messagebox.showerror("Input Error", "ADDRESS and DATA must be valid hexadecimal numbers.")
    except Exception as e:
        messagebox.showerror("Error", str(e))

def read_i2c():
    try:
        reg_str = entry_i2c_reg.get()
        if not reg_str:
            messagebox.showerror("Input Error", "ADDRESS field cannot be empty for reading.")
            return
        reg = int(reg_str, 16)
        val = i2c_read(reg)
        
        if val is not None:
            log_text.insert(tk.END, f"[I2C READ] Reg=0x{reg:02X} -> Value=0x{val:02X}\n")
            entry_i2c_data.delete(0, tk.END)
            entry_i2c_data.insert(0, f"{val:02X}")
        else:
            log_text.insert(tk.END, f"[I2C READ] Reg=0x{reg:02X} -> Read failed. No data received.\n")
        
        log_text.see(tk.END)
    except ValueError:
        messagebox.showerror("Input Error", "ADDRESS must be a valid hexadecimal number.")
    except Exception as e:
        messagebox.showerror("Error", str(e))

def send_pulse_command():
    """Sends the command to generate a specific number of pulses to the active controller."""
    try:
        count_str = entry_pulse_count.get()
        if not count_str:
            messagebox.showerror("Input Error", "Pulse count cannot be empty.")
            return
        
        count = int(count_str)
        if not (0 < count <= 9000):
            messagebox.showerror("Input Error", "Pulse count must be between 1 and 9000.")
            return

        if not send_pulse_command_internal(count):
            messagebox.showerror("Connection Error", "Arduino or Nordic_mcu not connected.")
            return

        log_text.insert(tk.END, f"[PULSE] Sent command to generate {count} pulses.\n")
        log_text.see(tk.END)

    except ValueError:
        messagebox.showerror("Input Error", "Pulse count must be a valid integer.")
    except Exception as e:
        messagebox.showerror("Error", str(e))

def clear_log():
    log_text.delete("1.0", tk.END)

def clear_console_input():
    console_text_input.delete("1.0", tk.END)
def run_console_cmd():
    """?? ??? ??(W/R/T/D)? ???? ??."""
    if not console_text_input:
        messagebox.showerror("Console Error", "Console input is not available.")
        return
    cmds = console_text_input.get("1.0", tk.END).strip().splitlines()
    if not cmds:
        return

    def worker():
        for cmd_line in cmds:
            line = cmd_line.strip()
            if not line:
                continue
            root.after(0, lambda l=line: (log_text.insert(tk.END, f"> {l}\n"), log_text.see(tk.END)))
            try:
                parts = line.split("//")[0].strip().split(",")
                if not parts or not parts[0]:
                    continue
                cmd = parts[0].upper()
                if cmd == "W" and len(parts) >= 3:
                    reg, data = int(parts[1], 16), int(parts[2], 16)
                    resp = i2c_write(reg, data)
                    root.after(0, lambda r=reg, d=data, rs=resp: log_text.insert(tk.END, f"[I2C WRITE] 0x{r:02X} <- 0x{d:02X} ({rs})\n"))
                elif cmd == "R" and len(parts) >= 2:
                    reg = int(parts[1], 16)
                    val = i2c_read(reg)
                    if val is not None:
                        root.after(0, lambda r=reg, v=val: log_text.insert(tk.END, f"[I2C READ] 0x{r:02X} -> 0x{v:02X}\n"))
                    else:
                        root.after(0, lambda r=reg: log_text.insert(tk.END, f"[I2C READ] 0x{r:02X} -> Read Failed\n"))
                elif cmd == "T":
                    if len(parts) > 1:
                        count = int(parts[1])
                        if send_pulse_command_internal(count):
                            root.after(0, lambda c=count: log_text.insert(tk.END, f"[PULSE] Sent command to generate {c} pulses via console.\n"))
                        else:
                            root.after(0, lambda: log_text.insert(tk.END, "[ERROR] Failed to send pulse command (controller missing).\n"))
                    else:
                        root.after(0, lambda: log_text.insert(tk.END, "[ERROR] Pulse count missing for T command.\n"))
                elif cmd == "D":
                    if len(parts) > 1:
                        delay_ms = int(parts[1])
                        root.after(0, lambda d=delay_ms: log_text.insert(tk.END, f"[DELAY] Waiting for {d} ms...\n"))
                        time.sleep(delay_ms / 1000.0)
                        root.after(0, lambda: log_text.insert(tk.END, "[DELAY] ...Done.\n"))
                    else:
                        root.after(0, lambda: log_text.insert(tk.END, "[ERROR] Delay time missing for D command.\n"))
                else:
                    root.after(0, lambda: log_text.insert(tk.END, "[ERROR] Unknown or malformed command\n"))
            except Exception as e:
                root.after(0, lambda m=str(e): log_text.insert(tk.END, f"[ERROR] {m}\n"))
        root.after(0, log_text.see, tk.END)

    threading.Thread(target=worker, daemon=True).start()

def _set_measurement_context(ctx):
    global current_measurement_context
    current_measurement_context = ctx

def run_rd_sample_num():
    """
    Reads the pre-programmed sample number from the IC and populates
    the 'IC Sample Num' entry field.
    """
    controller_connected = (Nordic_mcu and Nordic_mcu.running) or arduino
    if not controller_connected:
        messagebox.showerror("Connection Error", "Please connect a Controller (Arduino/Nordic_mcu).")
        return

    def worker():
        try:
            root.after(0, lambda: log_text.insert(tk.END, "[CMD] Reading Sample Number...\n"))
            root.after(0, lambda: log_text.see(tk.END))

            # 1. W,0x19,0x02 (Software Reset)
            i2c_write(0x19, 0x02)
            root.after(0, lambda: log_text.insert(tk.END, "  W,0x19,0x02 (SW Reset)\n"))

            # 2. D,2 (Delay 2ms)
            time.sleep(2 / 1000.0)

            # 3. W,0x55,0xA1 (TEST_KEY)
            i2c_write(0x55, 0xA1)
            root.after(0, lambda: log_text.insert(tk.END, "  W,0x55,0xA1 (TEST_KEY)\n"))

            # 4. W,0x56,0xA2 (NVM_KEY)
            i2c_write(0x56, 0xA2)
            root.after(0, lambda: log_text.insert(tk.END, "  W,0x56,0xA2 (NVM_KEY)\n"))

            # 5. R,0x00 (Read chipID)
            chip_id = i2c_read(0x00)
            if chip_id is not None:
                root.after(0, lambda v=chip_id: log_text.insert(tk.END, f"  R,0x00 (Chip ID) -> 0x{v:02X}\n"))
            else:
                root.after(0, lambda: log_text.insert(tk.END, "  R,0x00 (Chip ID) -> Read Failed\n"))

            # 6. R,0x51 (Read SampleNum)
            sample_num_val = i2c_read(0x51)

            if sample_num_val is not None:
                root.after(0, lambda v=sample_num_val: log_text.insert(tk.END, f"  R,0x51 (Sample Num) -> 0x{v:02X} ({v})\n"))
                # 7. Update the GUI
                root.after(0, lambda v=sample_num_val: [
                    entry_sample_num.delete(0, tk.END),
                    entry_sample_num.insert(0, str(v)),
                    log_text.insert(tk.END, f"[INFO] 'IC Sample Num' field updated to: {v}\n")
                ])
            else:
                root.after(0, lambda: log_text.insert(tk.END, "  R,0x51 (Sample Num) -> Read Failed\n"))
                root.after(0, lambda: messagebox.showwarning("Read Error", "Failed to read Sample Number from 0x51."))

            root.after(0, lambda: log_text.see(tk.END))

        except Exception as e:
            root.after(0, lambda err=str(e): messagebox.showerror("Error", err))
            root.after(0, lambda err=str(e): log_text.insert(tk.END, f"[ERROR] {err}\n"))
            root.after(0, lambda: log_text.see(tk.END))

    threading.Thread(target=worker, daemon=True).start()

def _resolve_trim_script(context, show_error=True):
    """Returns the filename for a trim context after validating controller connectivity."""
    filename_map = {
        "bgr": "TRIM_BGR.txt",
        "mclk": "TRIM_MCLK.txt",
        "wclk": "TRIM_WCLK.txt",
        "led1": "TRIM_LED1.txt",
        "led2": "TRIM_LED2.txt",
        "tc": "TRIM_TC.txt",
    }
    
    filename = filename_map.get(context)
    if not filename:
        log_text.insert(tk.END, f"[ERROR] Unknown trim context: {context}\n")
        return None
        
    controller_connected = (Nordic_mcu and Nordic_mcu.running) or arduino
    if not controller_connected:
        if show_error:
            messagebox.showerror("Connection Error", "Please connect a Controller (Arduino/Nordic_mcu).")
        return None

    return filename

def run_trim_script(context):
    """Sets the context and runs the corresponding trim setup script in a thread."""
    global last_script_context
    filename = _resolve_trim_script(context)
    if not filename:
        return
    last_script_context = context
    log_text.insert(tk.END, f"[SCRIPT] Setting up for {context.upper()} measurement by running {filename}...\n")
    # Use the new worker that handles relays and measurements
    worker_thread = threading.Thread(target=script_runner_worker, args=(context, filename), daemon=True)
    worker_thread.start()

def run_all_trim_scripts():
    """Runs all trim scripts sequentially in the defined order."""
    global last_script_context, run_all_searches_button
    contexts = ["bgr", "mclk", "wclk", "tc", "led1", "led2"] # TC(스크립트) 우선, 그 다음 LED1 -> LED2
    if not ((Nordic_mcu and Nordic_mcu.running) or arduino):
        messagebox.showerror("Connection Error", "Please connect a Controller (Arduino/Nordic_mcu).")
        return

    def worker():
        try:
            for ctx in contexts:
                filename = _resolve_trim_script(ctx, show_error=False)
                if not filename:
                    root.after(0, lambda c=ctx: log_text.insert(tk.END, f"[ERROR] Could not resolve script for context: {c}\n"))
                    break
                def log_start():
                    log_text.insert(tk.END, f"[SCRIPT] (Batch) Starting {ctx.upper()} sequence...\n")
                    log_text.see(tk.END)
                root.after(0, log_start)
                script_runner_worker(ctx, filename, is_batch=True)
                root.after(0, lambda c=ctx: log_text.insert(tk.END, f"[SCRIPT] (Batch) Finished {c.upper()} sequence.\n"))
        finally:
            if run_all_searches_button:
                root.after(0, lambda: run_all_searches_button.config(state=tk.NORMAL))

    if run_all_searches_button:
        run_all_searches_button.config(state=tk.DISABLED)
    threading.Thread(target=worker, daemon=True).start()

def script_runner_worker(context, filename, is_batch=False):
    """Executes a script, controls relays, and triggers measurements in a thread."""
    is_led_or_tc = context in ["led1", "led2", "tc"]
    try:
        if not is_batch:
            global last_script_context
            last_script_context = context
        if is_led_or_tc:
           # pwr_on()
            time.sleep(2.0) # Wait for PSU to stabilize

        execute_script_from_thread(filename)

        if context == "bgr":
            root.after(0, lambda: control_arduino_relay(1, "ON"))
            time.sleep(0.5)
            _set_measurement_context("bgr")
            root.after(0, measure_voltage2)
            time.sleep(1.0)
            root.after(0, lambda: control_arduino_relay(1, "OFF"))

        elif context in ["wclk", "mclk"]:
            _set_measurement_context(context)
            root.after(50, measure_freq2)

        elif is_led_or_tc:
            if context == "led2":
                root.after(0, lambda: control_arduino_relay(2, "ON"))
            else: # led1 and tc
                root.after(0, lambda: control_arduino_relay(2, "OFF"))
            
            time.sleep(0.5) # Wait for relay
            _set_measurement_context(context)
            root.after(50, measure_current)
            time.sleep(3.5) # Wait for measurement and potential other actions

    except Exception as e:
        root.after(0, lambda: messagebox.showerror("Script Worker Error", f"An error occurred while running {filename}:\n{e}"))
    finally:
        if is_led_or_tc:
            root.after(0, lambda: control_arduino_relay(2, "OFF")) # Always restore relay 2
            if power_supply:
                pwr_off()

# ------------------------------
# Automated Search and NVM Programming
# ------------------------------
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # Not running in a bundle, so use the script's directory
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)

def execute_script_from_thread(filename, file_handle=None):
    """Helper to run script commands from a thread, logging to GUI and optionally a file."""
    try:
        base_path = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_path, filename)
        file_path = resource_path(filename)
        with open(file_path, 'r') as f:
            script_content = f.read()
        
        cmds = script_content.strip().splitlines()
        for cmd_line in cmds:
            # This is a simplified version of run_console_cmd for thread safety
            # It directly sends commands and logs, assuming correct format
            parts = cmd_line.split("//")[0].strip().split(",")
            cmd = parts[0].upper()
            
            log_line = f"> {cmd_line}\n"
            if file_handle: file_handle.write(log_line)
            def update_log(line):
                log_text.insert(tk.END, line)
                log_text.see(tk.END)

            root.after(0, lambda l=log_line: update_log(l))
            
            # Execute command
            if cmd == "W":
                reg, data = int(parts[1], 16), int(parts[2], 16)
                i2c_write(reg, data)
            elif cmd == "D":
                delay_ms = int(parts[1])
                time.sleep(delay_ms / 1000.0)
            elif cmd == "T":
                count = int(parts[1])
                send_pulse_command_internal(count)
            # Add other simple commands if needed, but avoid complex GUI interactions
            time.sleep(0.05) # Small delay between script commands
    except Exception as e:
        root.after(0, lambda err=e: messagebox.showerror("Script Error", f"Error executing {filename}:\n{err}"))


def bgr_search_worker(sample_num):
    """The actual BGR search logic that runs in a separate thread."""
    try:
        global best_bgr_code
        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        results = []

        with open(file_path, "w") as f:
            f.write(f"--- IC Sample Num: {sample_num} ---\n\n")
            
            # --- BGR Search ---
            f.write("--- BGR Search Results ---\n")
            execute_script_from_thread("TRIM_BGR.txt", f)

            # Turn Relay 1 ON before starting the search loop
            root.after(0, lambda: control_arduino_relay(1, "ON"))

            if multimeter2:
                multimeter2.configure_voltage()

            #for i in range(0, 128):
            for i in range(20, 80):  # Narrowed range for faster testing
                i2c_write(0x3F, 0x01); i2c_write(0x40, i); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                raw_value = multimeter2.get_fast_measurement() if multimeter2 else None
                log_line = ""
                if raw_value:
                    try:
                        numeric_value = float(raw_value.split(',')[-1])
                        results.append({'code': i, 'voltage': numeric_value})
                        log_line = f"BGR CODE {i}, {numeric_value:.6f} V\n"
                    except (ValueError, IndexError):
                        log_line = f"BGR CODE {i}, Parse Error\n"
                else:
                    log_line = f"BGR CODE {i}, DMM Error\n"
                
                f.write(log_line)
                def update_gui(line_to_log):
                    log_text.insert(tk.END, line_to_log)
                    log_text.see(tk.END)
                root.after(0, lambda line=log_line: update_gui(line))

            best_code, best_voltage, analysis_result = -1, 0.0, ""
            if not results:
                analysis_result = "[ANALYSIS] No valid BGR data collected.\n"
            else:
                target_voltage = 1.115
                min_diff = float('inf')
                for res in results:
                    diff = abs(res['voltage'] - target_voltage)
                    if diff < min_diff:
                        min_diff = diff
                        best_code = res['code']
                        best_voltage = res['voltage']
                best_bgr_code = best_code
                analysis_result = (
                    f"\n[ANALYSIS] Target: {target_voltage:.3f} V\n"
                    f"[ANALYSIS] Best BGR code found: {best_code} (0x{best_code:02X}) with Voltage: {best_voltage:.6f} V\n"
                )
            f.write(analysis_result)

        def final_message(result_text, final_voltage, final_code):
            log_text.insert(tk.END, f"\n[INFO] BGR Search complete. Results saved to {filename}\n")
            log_text.insert(tk.END, result_text)
            log_text.see(tk.END)
            bgr_search_button.config(state=tk.NORMAL)
            if final_code != -1:
                bgr_result_var.set(f"{final_voltage:.3f} V")
                bgr_trim_code_var.set(f"{final_code} / 0x{final_code:02X}")
        root.after(0, lambda: final_message(analysis_result, best_voltage, best_code))

    except Exception as e:
        def error_message():
            messagebox.showerror("BGR Search Error", str(e))
            bgr_search_button.config(state=tk.NORMAL)
        root.after(0, error_message)
    finally:
        # Ensure Relay 1 is turned OFF after the search is complete or if an error occurs
        root.after(0, lambda: control_arduino_relay(1, "OFF"))

def mclk_search_worker(sample_num):
    """The actual MCLK search logic that runs in a separate thread."""
    try:
        global best_mclk_code, best_bgr_code
        if best_bgr_code == -1:
            def show_error():
                messagebox.showerror("Prerequisite Error", "Run BGR Search first to find the best BGR code.")
                mclk_search_button.config(state=tk.NORMAL)
            root.after(0, show_error)
            return

        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        results = []
        TARGET_MCLK_HZ = 312500.0

        with open(file_path, "a") as f:
            f.write("\n--- MCLK Search Results ---\n")
            execute_script_from_thread("TRIM_MCLK.txt", f)

            if multimeter2:
                multimeter2.configure_freq()

            #for i in range(0, 128):
            for i in range(20, 48):  # Narrowed range for faster testing
                # Apply best BGR code first for accurate measurement
                if best_bgr_code != -1:
                    i2c_write(0x3F, 0x01); i2c_write(0x40, best_bgr_code); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3F, 0x02); i2c_write(0x40, i); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                raw_value = multimeter2.get_fast_measurement(wait_init=0.4, wait_fetc=0.4) if multimeter2 else None
                log_line = ""
                if raw_value:
                    try:
                        numeric_value = float(raw_value.split(',')[-1])
                        results.append({'code': i, 'freq': numeric_value})
                        log_line = f"MCLK CODE {i}, {numeric_value/1000:.3f} kHz\n"
                    except (ValueError, IndexError):
                        log_line = f"MCLK CODE {i}, Parse Error\n"
                else:
                    log_line = f"MCLK CODE {i}, DMM Error\n"
                
                f.write(log_line)
                def update_gui(line_to_log):
                    log_text.insert(tk.END, line_to_log)
                    log_text.see(tk.END)
                root.after(0, lambda line=log_line: update_gui(line))
            
            best_code, best_freq, analysis_result = -1, 0.0, ""
            if not results:
                analysis_result = "[ANALYSIS] No valid MCLK data.\n"
            else:
                min_diff = float('inf')
                for res in results:
                    diff = abs(res['freq'] - TARGET_MCLK_HZ)
                    if diff < min_diff:
                        min_diff = diff
                        best_code = res['code']
                        best_freq = res['freq']
                best_mclk_code = best_code
                analysis_result = (
                    f"\n[ANALYSIS] Target: {TARGET_MCLK_HZ/1000:.3f} kHz\n"
                    f"[ANALYSIS] Best MCLK code found: {best_code} (0x{best_code:02X}) with Frequency: {best_freq/1000:.3f} kHz\n"
                )
            f.write(analysis_result)

        def final_message(result_text, final_freq, final_code):
            log_text.insert(tk.END, f"\n[INFO] MCLK Search complete. Results appended to {filename}\n")
            log_text.insert(tk.END, result_text)
            log_text.see(tk.END)
            mclk_search_button.config(state=tk.NORMAL)
            if final_code != -1:
                mclk_result_var.set(f"{final_freq/1000:.3f} kHz")
                mclk_trim_code_var.set(f"{final_code} / 0x{final_code:02X}")
        root.after(0, lambda: final_message(analysis_result, best_freq, best_code))

    except Exception as e:
        root.after(0, lambda err=e: messagebox.showerror("MCLK Search Error", str(err)))
        root.after(0, lambda: mclk_search_button.config(state=tk.NORMAL))

def wclk_search_worker(sample_num):
    """The actual WCLK search logic that runs in a separate thread."""
    try:
        global best_wclk_code

        if best_bgr_code == -1:
            root.after(0, lambda: messagebox.showerror("Prerequisite Error", "Run BGR Search first."))
            root.after(0, lambda: wclk_search_button.config(state=tk.NORMAL))
            return

        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"

        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        results = []
        TARGET_WCLK_HZ = 10000.0

        with open(file_path, "a") as f:
            f.write("\n--- WCLK Search Results ---\n")
            execute_script_from_thread("TRIM_WCLK.txt", f)

            if multimeter2:
                multimeter2.configure_freq()

            for i in range(32):
                # Apply best BGR code first for accurate measurement
                i2c_write(0x3F, 0x01); i2c_write(0x40, best_bgr_code); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3F, 0x03); i2c_write(0x40, i); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                raw_value = multimeter2.get_fast_measurement(wait_init=0.4, wait_fetc=0.4) if multimeter2 else None
                log_line = ""
                if raw_value:
                    try:
                        numeric_value = float(raw_value.split(',')[-1])
                        results.append({'code': i, 'freq': numeric_value})
                        log_line = f"WCLK CODE {i}, {numeric_value/1000:.3f} kHz\n"
                    except (ValueError, IndexError):
                        log_line = f"WCLK CODE {i}, Parse Error\n"
                else:
                    log_line = f"WCLK CODE {i}, DMM Error\n"
                
                f.write(log_line)
                def update_gui(line_to_log):
                    log_text.insert(tk.END, line_to_log)
                    log_text.see(tk.END)
                root.after(0, lambda line=log_line: update_gui(line))

            best_code, best_freq, analysis_result = -1, 0.0, ""
            if not results:
                analysis_result = "[ANALYSIS] No valid WCLK data.\n"
            else:
                min_diff = float('inf')
                for res in results:
                    diff = abs(res['freq'] - TARGET_WCLK_HZ)
                    if diff < min_diff:
                        min_diff = diff
                        best_code = res['code']
                        best_freq = res['freq']
                best_wclk_code = best_code
                analysis_result = (
                    f"\n[ANALYSIS] Target: {TARGET_WCLK_HZ/1000:.3f} kHz\n"
                    f"[ANALYSIS] Best WCLK code found: {best_code} (0x{best_code:02X}) with Frequency: {best_freq/1000:.3f} kHz\n"
                )
            f.write(analysis_result)

        def final_message(result_text, final_freq, final_code):
            log_text.insert(tk.END, f"\n[INFO] WCLK Search complete. Results appended to {filename}\n")
            log_text.insert(tk.END, result_text)
            log_text.see(tk.END)
            wclk_search_button.config(state=tk.NORMAL)
            if final_code != -1:
                wclk_result_var.set(f"{final_freq/1000:.3f} kHz")
                wclk_trim_code_var.set(f"{final_code} / 0x{final_code:02X}")
        root.after(0, lambda: final_message(analysis_result, best_freq, best_code))

    except Exception as e:
        root.after(0, lambda err=e: messagebox.showerror("WCLK Search Error", str(err)))
        root.after(0, lambda: wclk_search_button.config(state=tk.NORMAL))

def led1_search_worker(sample_num):
    """The actual LED1 search logic that runs in a separate thread."""
    try:
        global best_led1_code, best_bgr_code
        if best_bgr_code == -1:
            root.after(0, lambda: messagebox.showerror("Prerequisite Error", "Run BGR Search first."))
            root.after(0, lambda: led1_search_button.config(state=tk.NORMAL))
            return
        #pwr_on()
        root.after(0, lambda: control_arduino_relay(2, "OFF"))
        time.sleep(2.0) # Wait for PSU and relay to stabilize

        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        results = []
        #TARGET_CURRENT_A = 0.0618 # 61.8mA
        TARGET_CURRENT_A = 0.0310 # 31.8mA

        with open(file_path, "a") as f:
            f.write("\n--- LED1 Search Results ---\n")

            if multimeter:
                multimeter.configure_current()

            #for i in range(0, 64):
            for i in range(15, 37):  # LED1 코드 범위 축소: 15~37
                i2c_write(0x19, 0x02); time.sleep(0.01)
                i2c_write(0x55, 0xA1); i2c_write(0x56, 0xA2)
                i2c_write(0x3F, 0x01); i2c_write(0x40, best_bgr_code); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x02, 0x00); i2c_write(0x3A, 0x31)
                #i2c_write(0x06, 0x7F); i2c_write(0x1D, 0xFF)  #61.8 mA 한도 
                i2c_write(0x06, 0x7F); i2c_write(0x1D, 0x7F) #31.0 mA 한도
                i2c_write(0x65, 0x01); i2c_write(0x66, 0x01)
                i2c_write(0x5C, 0x01); i2c_write(0x5D, 0xBF); i2c_write(0x5E, 0x1F)
                i2c_write(0x3F, 0x04); i2c_write(0x40, i); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3C, 0x8B); i2c_write(0x6E, 0x43)
                i2c_write(0x01, 0x01); time.sleep(0.01)
                
                # Nordic/pipe 우선: ACK까지 기다린 뒤 바로 측정
                raw_value = None
                if send_pulse_command_internal(3200):
                    raw_value = multimeter.get_fast_measurement(wait_init=0.2, wait_fetc=0.7) if multimeter else None
                
                log_line = ""
                if raw_value:
                    try:
                        numeric_value = float(raw_value.split(',')[-1])
                        results.append({'code': i, 'current': numeric_value})
                        log_line = f"LED1 CODE {i}, {numeric_value * 1000:.3f} mA\n"
                    except (ValueError, IndexError):
                        log_line = f"LED1 CODE {i}, Parse Error\n"
                else:
                    log_line = f"LED1 CODE {i}, DMM read failed\n"
                
                f.write(log_line)
                root.after(0, lambda line=log_line: (log_text.insert(tk.END, line), log_text.see(tk.END)))

            best_code, best_current, analysis_result = -1, 0.0, ""
            if not results:
                analysis_result = "[ANALYSIS] No valid LED1 data collected.\n"
            else:
                best_res = min(results, key=lambda x: abs(x['current'] - TARGET_CURRENT_A))
                best_code = best_res['code']
                best_current = best_res['current']
                best_led1_code = best_code
                analysis_result = (
                    f"\n[ANALYSIS] Target: {TARGET_CURRENT_A * 1000:.1f} mA\n"
                    f"[ANALYSIS] Best LED1 code found: {best_code} (0x{best_code:02X}) with Current: {best_current * 1000:.3f} mA\n"
                )
            f.write(analysis_result)

        def final_update():
            log_text.insert(tk.END, f"\n[INFO] LED1 Search complete. Results appended to {filename}\n")
            log_text.insert(tk.END, analysis_result)
            log_text.see(tk.END)
            if best_code != -1:
                led1_result_var.set(f"{best_current * 1000:.3f} mA")
                led1_trim_code_var.set(f"{best_code} / 0x{best_code:02X}")
        root.after(0, final_update)

    except Exception as e:
        root.after(0, lambda err=e: messagebox.showerror("LED1 Search Error", f"An error occurred: {err}"))
    finally:
        root.after(0, lambda: led1_search_button.config(state=tk.NORMAL))
        if power_supply:
            pwr_off()

def led2_search_worker(sample_num):
    """The actual LED2 search logic that runs in a separate thread."""
    try:
        global best_led2_code, best_bgr_code
        if best_bgr_code == -1:
            root.after(0, lambda: messagebox.showerror("Prerequisite Error", "Run BGR Search first."))
            root.after(0, lambda: led2_search_button.config(state=tk.NORMAL))
            return
        #pwr_on()
        root.after(0, lambda: control_arduino_relay(2, "ON"))

        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        results = []
        #TARGET_CURRENT_A = 0.0618 # 61.8mA
        TARGET_CURRENT_A = 0.0310 # 31.0mA

        time.sleep(2.0)

        with open(file_path, "a") as f:
            f.write("\n--- LED2 Search Results ---\n")

            if multimeter:
                multimeter.configure_current()

            #for i in range(0, 64):
            for i in range(15, 37):  # LED2 코드 범위 축소: 15~37
                i2c_write(0x19, 0x02); time.sleep(0.01)
                i2c_write(0x55, 0xA1); i2c_write(0x56, 0xA2)
                i2c_write(0x3F, 0x01); i2c_write(0x40, best_bgr_code); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x02, 0x01); i2c_write(0x3A, 0x31)
                #i2c_write(0x06, 0x7F); i2c_write(0x1E, 0xFF)  #61.8 mA 한도
                i2c_write(0x06, 0x7F); i2c_write(0x1E, 0x7F)  #31.0 mA 한도
                i2c_write(0x65, 0x01); i2c_write(0x66, 0x01)
                i2c_write(0x5C, 0x01); i2c_write(0x5D, 0xBF); i2c_write(0x5E, 0x1F)
                i2c_write(0x3F, 0x05); i2c_write(0x40, 64 + i); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3C, 0x8B); i2c_write(0x6E, 0x43)
                i2c_write(0x01, 0x01); time.sleep(0.01)
                
                if send_pulse_command_internal(5080): # Wait for ACK from pulse command
                    raw_value = multimeter.get_fast_measurement(wait_init=0.2, wait_fetc=0.7) if multimeter else None
                
                log_line = ""
                if raw_value:
                    try:
                        numeric_value = float(raw_value.split(',')[-1])
                        results.append({'code': i, 'current': numeric_value})
                        log_line = f"LED2 CODE {i}, {numeric_value * 1000:.3f} mA\n"
                    except (ValueError, IndexError):
                        log_line = f"LED2 CODE {i}, Parse Error\n"
                else:
                    log_line = f"LED2 CODE {i}, DMM read failed\n"
                
                f.write(log_line)
                root.after(0, lambda line=log_line: (log_text.insert(tk.END, line), log_text.see(tk.END)))

            best_code, best_current, analysis_result = -1, 0.0, ""
            if not results:
                analysis_result = "[ANALYSIS] No valid LED2 data collected.\n"
            else:
                best_res = min(results, key=lambda x: abs(x['current'] - TARGET_CURRENT_A))
                best_code = best_res['code']
                best_current = best_res['current']
                best_led2_code = best_code
                analysis_result = (
                    f"\n[ANALYSIS] Target: {TARGET_CURRENT_A * 1000:.1f} mA\n"
                    f"[ANALYSIS] Best LED2 code found: {best_code} (0x{best_code:02X}) with Current: {best_current * 1000:.3f} mA\n"
                )
            f.write(analysis_result)

        def final_update():
            log_text.insert(tk.END, f"\n[INFO] LED2 Search complete. Results appended to {filename}\n")
            log_text.insert(tk.END, analysis_result)
            log_text.see(tk.END)
            if best_code != -1:
                led2_result_var.set(f"{best_current * 1000:.3f} mA")
                led2_trim_code_var.set(f"{best_code} / 0x{best_code:02X}")
        root.after(0, final_update)

    except Exception as e:
        root.after(0, lambda err=e: messagebox.showerror("LED2 Search Error", f"An error occurred: {err}"))
    finally:
        root.after(0, lambda: led2_search_button.config(state=tk.NORMAL))
        if power_supply:
            pwr_off()
        root.after(0, lambda: control_arduino_relay(2, "OFF"))

def tc_search_worker(sample_num, target_current_amps):
    """The actual TC search logic that runs in a separate thread."""
    try:
        global best_tc_code
        #pwr_on()
        root.after(0, lambda: control_arduino_relay(2, "OFF"))
        time.sleep(2.0)

        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        results = []
        TARGET_CURRENT_A = target_current_amps

        with open(file_path, "a") as f:
            f.write("\n--- TC Search Results ---\n")

            if multimeter:
                multimeter.configure_current()

            for i in range(0, 32):
                i2c_write(0x19, 0x02); time.sleep(0.01)
                i2c_write(0x55, 0xA1); i2c_write(0x56, 0xA2)
                i2c_write(0x02, 0x00); i2c_write(0x3A, 0x31)
                #i2c_write(0x06, 0x7F); i2c_write(0x1D, 0xFF)   #61.8 mA 한도
                i2c_write(0x06, 0x7F); i2c_write(0x1D, 0x3F)    #31.0 mA 한도
                i2c_write(0x65, 0x01); i2c_write(0x66, 0x01)
                i2c_write(0x5C, 0x01); i2c_write(0x5D, 0xBF); i2c_write(0x5E, 0x1F)
                i2c_write(0x3F, 0x04); i2c_write(0x40, 0x20); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3F, 0x05); i2c_write(0x40, 0x60); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3F, 0x06); i2c_write(0x40, 64 + i); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3F, 0x07); i2c_write(0x40, 0x3F); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3C, 0x8B); i2c_write(0x6E, 0x43)
                i2c_write(0x01, 0x01); time.sleep(0.01)
                
                raw_value = None
                if send_pulse_command_internal(3200):
                    raw_value = multimeter.get_fast_measurement(wait_init=0.2, wait_fetc=0.7) if multimeter else None
                
                log_line = ""
                if raw_value:
                    try:
                        numeric_value = float(raw_value.split(',')[-1])
                        results.append({'code': i, 'current': numeric_value})
                        log_line = f"TC CODE {i}, {numeric_value * 1000:.3f} mA\n"
                    except (ValueError, IndexError):
                        log_line = f"TC CODE {i}, Parse Error\n"
                else:
                    log_line = f"TC CODE {i}, DMM read failed\n"

                f.write(log_line)
                root.after(0, lambda line=log_line: (log_text.insert(tk.END, line), log_text.see(tk.END)))

            best_code, best_current, analysis_result = -1, 0.0, ""
            if not results:
                analysis_result = "[ANALYSIS] No valid TC data collected.\n"
            else:
                best_res = min(results, key=lambda x: abs(x['current'] - TARGET_CURRENT_A))
                best_code = best_res['code']
                best_current = best_res['current']
                best_tc_code = best_code
                analysis_result = (
                    f"\n[ANALYSIS] Target: {TARGET_CURRENT_A * 1000:.1f} mA\n"
                    f"[ANALYSIS] Best TC code found: {best_code} (0x{best_code:02X}) with Current: {best_current * 1000:.3f} mA\n"
                )
            f.write(analysis_result)

        def final_update():
            log_text.insert(tk.END, f"\n[INFO] TC Search complete. Results appended to {filename}\n")
            log_text.insert(tk.END, analysis_result)
            log_text.see(tk.END)
            if best_code != -1:
                tc_min_result_var.set(f"{best_current * 1000:.3f} mA")
                tc_trim_code_var.set(f"{best_code} / 0x{best_code:02X}")
        root.after(0, final_update)

    except Exception as e:
        root.after(0, lambda err=e: messagebox.showerror("TC Search Error", f"An error occurred: {err}"))
    finally:
        root.after(0, lambda: tc_search_button.config(state=tk.NORMAL))
        if power_supply:
            pwr_off()

def tc_search_worker_m2(sample_num, target_current_amps):
    """
    TC Search Method 2: 두 번 측정해서 차이가 가장 작은 코드를 선택 (TRIM_v05_test 방식).
    """
    try:
        global best_tc_code, best_bgr_code
        if best_bgr_code == -1:
            root.after(0, lambda: messagebox.showerror("Prerequisite Error", "Run BGR Search first."))
            root.after(0, lambda: tc_search_m2_button.config(state=tk.NORMAL))
            return
        best_tc_code = -1

        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        results = []

        with open(file_path, "a") as f:
            f.write("\n--- TC Search Results (Method 2 - Minimum Difference) ---\n")

            # PSU ON (고정값 사용)
            # try:
            #     ser = power_supply
            #     voltage = 1.8
            #     current = 0.5
            #     log_text.insert(tk.END, f"[PSU] Setting ON. V={voltage}V, I={current}A\n")
            #     ser.write(b'*CLS\n'); time.sleep(0.05)
            #     ser.write(b'*RST\n'); time.sleep(0.05)
            #     ser.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
            #     ser.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
            #     ser.write(b'OUTP ON\n'); time.sleep(0.2)
            #     time.sleep(2.0)
            #     log_text.insert(tk.END, "[PSU] Output is ON\n")

            #     root.update_idletasks()
            # except Exception as e:
            #     messagebox.showerror("PSU Error", str(e))
            #     return

            # DMM 설정
           
            if multimeter:
                multimeter.configure_current()

            for i in range(16, 32):
                root.after(0, lambda c=i: (log_text.insert(tk.END, f"--- Running sequence for TC code {c} ---\n"), log_text.see(tk.END)))

                # 측정 1 (DN: TC_SEL_LED0=0x1F)
                i2c_write(0x19, 0x02); time.sleep(0.01)
                i2c_write(0x55, 0xA1); i2c_write(0x56, 0xA2)
                i2c_write(0x3F, 0x01); i2c_write(0x40, best_bgr_code); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x02, 0x00); i2c_write(0x3A, 0x31)
                #i2c_write(0x06, 0x7F); i2c_write(0x1D, 0xFF)   #61.8 mA 한도       
                i2c_write(0x06, 0x7F); i2c_write(0x1D, 0x7F)    #31.0 mA 한도
                i2c_write(0x65, 0x01); i2c_write(0x66, 0x01)
                i2c_write(0x5C, 0x01); i2c_write(0x5D, 0xBF); i2c_write(0x5E, 0x1F)
                i2c_write(0x3F, 0x04); i2c_write(0x40, 0x20); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3F, 0x05); i2c_write(0x40, 0x60); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3F, 0x06); i2c_write(0x40, 64+i); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3F, 0x07); i2c_write(0x40, 0x1F); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3C, 0x8B); i2c_write(0x6E, 0x43); i2c_write(0x01, 0x01)
                raw_value_1 = None
                if send_pulse_command_internal(3200):
                    raw_value_1 = multimeter.get_fast_measurement(wait_init=0.2, wait_fetc=0.7) if multimeter else None

                # 측정 2 (UP: TC_SEL_LED0=0x3F)
                i2c_write(0x19, 0x02); time.sleep(0.01)
                i2c_write(0x55, 0xA1); i2c_write(0x56, 0xA2)
                i2c_write(0x3F, 0x01); i2c_write(0x40, best_bgr_code); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x02, 0x00); i2c_write(0x3A, 0x31)
                #i2c_write(0x06, 0x7F); i2c_write(0x1D, 0xFF)   #61.8 mA 한도          
                i2c_write(0x06, 0x7F); i2c_write(0x1D, 0x7F)    #31.0 mA 한도
                i2c_write(0x65, 0x01); i2c_write(0x66, 0x01)
                i2c_write(0x5C, 0x01); i2c_write(0x5D, 0xBF); i2c_write(0x5E, 0x1F)
                i2c_write(0x3F, 0x04); i2c_write(0x40, 0x20); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3F, 0x05); i2c_write(0x40, 0x60); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3F, 0x06); i2c_write(0x40, 64+i); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3F, 0x07); i2c_write(0x40, 0x3F); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                i2c_write(0x3C, 0x8B); i2c_write(0x6E, 0x43); i2c_write(0x01, 0x01)
                raw_value_2 = None
                if send_pulse_command_internal(3200):
                    raw_value_2 = multimeter.get_fast_measurement(wait_init=0.2, wait_fetc=0.7) if multimeter else None

                log_line = ""
                numeric_value_1, numeric_value_2 = None, None

                try:
                    if raw_value_1: numeric_value_1 = float(raw_value_1.split(',')[-1])
                    if raw_value_2: numeric_value_2 = float(raw_value_2.split(',')[-1])

                    if numeric_value_1 is not None and numeric_value_2 is not None:
                        diff_current = abs(numeric_value_1 - numeric_value_2)
                        results.append({'code': i, 'diff': diff_current, 'val1': numeric_value_1, 'val2': numeric_value_2})
                        log_line = (f"  TC CODE {i}:\n"
                                    f"    -> M1 (DN): {numeric_value_1 * 1000:.3f} mA\n"
                                    f"    -> M2 (UP): {numeric_value_2 * 1000:.3f} mA\n"
                                    f"    -> Diff: {diff_current * 1000:.3f} mA\n")
                    else:
                        log_line = f"  TC CODE {i}: DMM read failed on one or both measurements.\n"
                except (ValueError, IndexError):
                    log_line = f"  TC CODE {i}: Parse Error on DMM value.\n"

                f.write(log_line)
                def update_gui(line_to_log):
                    log_text.insert(tk.END, line_to_log)
                    log_text.see(tk.END)
                root.after(0, lambda line=log_line: update_gui(line))

            # 결과 분석
            best_code, best_current, analysis_result = -1, 0.0, ""
            if not results:
                analysis_result = "[ANALYSIS] No valid TC data collected.\n"
            else:
                min_diff_value = float('inf')
                best_result_entry = None
                for res in results:
                    if res['diff'] < min_diff_value:
                        min_diff_value = res['diff']
                        best_code = res['code']
                        best_result_entry = res
                best_tc_code = best_code
                if best_result_entry:
                    avg_of_best_pair = (best_result_entry['val1'] + best_result_entry['val2']) / 2.0
                    best_current = avg_of_best_pair
                    analysis_result = (
                        f"\n[ANALYSIS] Best TC code (min difference) found: {best_code} (0x{best_code:02X})\n"
                        f"[ANALYSIS] -> Difference: {min_diff_value * 1000:.3f} mA\n"
                        f"[ANALYSIS] -> Average current: {best_current * 1000:.3f} mA\n"
                    )
                else:
                    analysis_result = "[ANALYSIS] Could not determine a best code.\n"
            f.write(analysis_result)

        def final_message(result_text, final_current, final_code):
            log_text.insert(tk.END, f"\n[INFO] TC Search (Method 2) complete. Results appended to {filename}\n")
            log_text.insert(tk.END, result_text)
            log_text.see(tk.END)
            if final_code != -1:
                tc_min_result_var.set(f"{final_current * 1000:.3f} mA")
                tc_trim_code_var.set(f"{final_code} / 0x{final_code:02X}")
        root.after(0, lambda: final_message(analysis_result, best_current, best_code))

    except Exception as e:
        def error_message():
            messagebox.showerror("TC Search (Method 2) Error", str(e))
        root.after(0, error_message)
    finally:
        root.after(0, lambda: tc_search_m2_button.config(state=tk.NORMAL))
        if power_supply:
            pwr_off()
        root.after(0, lambda: control_arduino_relay(2, "OFF"))

def run_bgr_search():
    """Starts the BGR search in a new thread."""
    controller_connected = (Nordic_mcu and Nordic_mcu.running) or arduino
    if not controller_connected or not multimeter2:
        messagebox.showerror("Connection Error", "Please connect a Controller (Arduino/Nordic_mcu) and Multimeter 2.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an 'IC Sample Num'.")
        return
    
    bgr_search_button.config(state=tk.DISABLED)
    log_text.insert(tk.END, f"[INFO] Starting BGR Search for Sample: {sample_num} (0-127)...\n")
    search_thread = threading.Thread(target=bgr_search_worker, args=(sample_num,), daemon=True)
    search_thread.start()

def run_led2_search():
    """Starts the TC search in a new thread."""
    controller_connected = (Nordic_mcu and Nordic_mcu.running) or arduino
    if not controller_connected or not multimeter or not power_supply:
        messagebox.showerror("Connection Error", "Please connect a Controller, Multimeter, and Power Supply.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an 'IC Sample Num'.")
        return

    log_text.insert(tk.END, f"[INFO] Starting LED2 Search for Sample: {sample_num} (16-48)...\n")
    led2_search_button.config(state=tk.DISABLED)
    search_thread = threading.Thread(target=led2_search_worker, args=(sample_num,), daemon=True)
    search_thread.start()

def run_mclk_search():
    """Starts the MCLK search in a new thread."""
    controller_connected = (Nordic_mcu and Nordic_mcu.running) or arduino
    if not controller_connected or not multimeter2:
        messagebox.showerror("Connection Error", "Please connect a Controller (Arduino/Nordic_mcu) and Multimeter 2.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an 'IC Sample Num'.")
        return

    log_text.insert(tk.END, f"[INFO] Starting MCLK Search for Sample: {sample_num} (16-64)...\n")
    mclk_search_button.config(state=tk.DISABLED)
    search_thread = threading.Thread(target=mclk_search_worker, args=(sample_num,), daemon=True)
    search_thread.start()

def run_wclk_search():
    """Starts the WCLK search in a new thread."""
    controller_connected = (Nordic_mcu and Nordic_mcu.running) or arduino
    if not controller_connected or not multimeter2:
        messagebox.showerror("Connection Error", "Please connect a Controller (Arduino/Nordic_mcu) and Multimeter 2.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an 'IC Sample Num'.")
        return

    log_text.insert(tk.END, f"[INFO] Starting WCLK Search for Sample: {sample_num} (0-31)...\n")
    wclk_search_button.config(state=tk.DISABLED)
    search_thread = threading.Thread(target=wclk_search_worker, args=(sample_num,), daemon=True)
    search_thread.start()

def run_tc_search():
    """Starts the TC search in a new thread."""
    controller_connected = (Nordic_mcu and Nordic_mcu.running) or arduino
    if not controller_connected or not multimeter or not power_supply:
        messagebox.showerror("Connection Error", "Please connect a Controller, Multimeter, and Power Supply.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an 'IC Sample Num'.")
        return

    target_current_amps = 0.0
    try:
        tc_min_str = tc_min_result_var.get()
        if not tc_min_str or "mA" not in tc_min_str:
            messagebox.showerror("Input Error", "Please run 'TRIM_TC' and 'Measure Current' first to get a target value (in mA).")
            return
        value_str, _ = tc_min_str.split()
        target_current_amps = float(value_str) / 1000.0
    except (ValueError, TypeError, IndexError):
        messagebox.showerror("Parse Error", f"Could not parse the TC MIN target value: '{tc_min_str}'.\nPlease run 'TRIM_TC' and 'Measure Current' again.")
        return
    
    tc_search_button.config(state=tk.DISABLED)
    log_text.insert(tk.END, f"[INFO] Starting TC Search for Sample: {sample_num} (0-31)...\n")
    search_thread = threading.Thread(target=tc_search_worker, args=(sample_num, target_current_amps,), daemon=True)
    search_thread.start()

def run_tc_search_m2():
    """Starts the TC search (method 2) in a new thread."""
    controller_connected = (Nordic_mcu and Nordic_mcu.running) or arduino
    if not controller_connected or not multimeter or not power_supply:
        messagebox.showerror("Connection Error", "Please connect a Controller, Multimeter, and Power Supply.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an 'IC Sample Num'.")
        return
    
    log_text.insert(tk.END, f"[INFO] Starting TC Search for Sample: {sample_num} (0-31)...\n")
    tc_search_m2_button.config(state=tk.DISABLED)
    search_thread = threading.Thread(target=tc_search_worker_m2, args=(sample_num, 0), daemon=True)
    search_thread.start()

# New function for running all searches sequentially
def run_all_searches_orchestrator(sample_num, button, pgm=False):
    """BGR -> MCLK -> WCLK -> TC(Method2) -> LED1 -> LED2 순서로 일괄 검색."""
    global last_script_context
    try:
        root.after(0, lambda: button.config(state=tk.DISABLED))
        root.after(0, lambda: log_text.insert(tk.END, f"[INFO] Starting ALL Searches for Sample: {sample_num}...\n"))
        root.after(0, log_text.see, tk.END)

        sequence = [
            ("bgr", "BGR", bgr_search_worker, lambda: best_bgr_code),
            ("mclk", "MCLK", mclk_search_worker, lambda: best_mclk_code),
            ("wclk", "WCLK", wclk_search_worker, lambda: best_wclk_code),
            ("tc", "TC (Method2)", lambda s: tc_search_worker_m2(s, 0), lambda: best_tc_code),
            ("led1", "LED1", led1_search_worker, lambda: best_led1_code),
            ("led2", "LED2", led2_search_worker, lambda: best_led2_code),
        ]
        for ctx, label, worker_fn, result_fn in sequence:
            root.after(0, lambda l=label: log_text.insert(tk.END, f"[INFO] --- Starting {l} Search ---\n"))
            last_script_context = ctx
            worker_fn(sample_num) # This call is synchronous within this thread
            if result_fn() == -1:
                root.after(0, lambda l=label: messagebox.showerror("All Searches Failed", f"{l} Search failed. Stopping all searches."))
                return

        root.after(0, lambda: log_text.insert(tk.END, "[INFO] ALL Searches completed successfully!\n"))
        if pgm:
            run_nvm_program()
    except Exception as e:
        root.after(0, lambda err=e: messagebox.showerror("All Searches Error", f"An unexpected error occurred during all searches:\n{err}"))
    finally:
        root.after(0, lambda: button.config(state=tk.NORMAL))
        root.after(0, log_text.see, tk.END)

def run_all_searches():
    controller_connected = (Nordic_mcu and Nordic_mcu.running) or arduino
    if not controller_connected or not multimeter or not multimeter2 or not power_supply:
        messagebox.showerror("Connection Error", "Please connect a Controller, Multimeter (COM5), Multimeter 2 (COM11), and Power Supply for all searches.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an 'IC Sample Num'.")
        return
    threading.Thread(target=run_all_searches_orchestrator, args=(sample_num, run_all_searches_button), daemon=True).start()

def run_all_searches2():
    """Triggers the 'Run ALL Searches & PGM' sequence for button 2."""
    controller_connected = (Nordic_mcu and Nordic_mcu.running) or arduino
    if not controller_connected or not multimeter or not multimeter2 or not power_supply:
        messagebox.showerror("Connection Error", "Please connect a Controller, Multimeter (COM5), Multimeter 2 (COM11), and Power Supply for all searches.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an 'IC Sample Num'.")
        return
    threading.Thread(target=run_all_searches_orchestrator, args=(sample_num, run_all_searches_button2), kwargs={"pgm": True}, daemon=True).start()

def run_led1_search():
    """Starts the LED1 search in a new thread."""
    controller_connected = (Nordic_mcu and Nordic_mcu.running) or arduino
    if not controller_connected or not multimeter or not power_supply:
        messagebox.showerror("Connection Error", "Please connect a Controller, Multimeter, and Power Supply.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an 'IC Sample Num'.")
        return
    
    led1_search_button.config(state=tk.DISABLED)
    log_text.insert(tk.END, f"[INFO] Starting LED1 Search for Sample: {sample_num} (16-48)...\n")
    search_thread = threading.Thread(target=led1_search_worker, args=(sample_num,), daemon=True)
    search_thread.start()

# ------------------------------
# NVM Programming Button Actions
# ------------------------------
def run_nvm_program():
    """Programs the best found trim codes for BGR, MCLK, WCLK, TC into NVM."""
    if -1 in [best_bgr_code, best_mclk_code, best_wclk_code, best_tc_code]:
        messagebox.showwarning("Prerequisite Error", "All searches (BGR, MCLK, WCLK, TC) must be completed first.")
        return
    def worker():
        
        sample_num_str = entry_sample_num.get().strip()
        if not sample_num_str:
            messagebox.showerror("Input Error", "Please enter an IC Sample Number.")
            return
        
        try:
            sample_num_int = int(sample_num_str)
            if not (0 <= sample_num_int <= 255):
                messagebox.showerror("Input Error", "Sample Number must be an integer between 0 and 255.")
                return
        except ValueError:
            messagebox.showerror("Input Error", "Sample Number must be a valid integer.")
            return
        
            # --- New code to get Sample Num M ---
        sample_num_m_str = entry_sample_num_m.get().strip()
        if not sample_num_m_str:
            messagebox.showerror("Input Error", "Please enter a Sample Num M.")
            return
        
        try:
            sample_num_m_int = int(sample_num_m_str)
            if not (0 <= sample_num_m_int <= 255):
                messagebox.showerror("Input Error", "Sample Num M must be an integer between 0 and 255.")
                return
        except ValueError:
            messagebox.showerror("Input Error", "Sample Num M must be a valid integer.")
            return
        # --- End of new code for M ---

        # --- New code to get Sample Num M ---
        sample_num_d_str = entry_sample_num_d.get().strip()
        if not sample_num_d_str:
            messagebox.showerror("Input Error", "Please enter a Sample Num D.")
            return
        
        try:
            sample_num_d_int = int(sample_num_d_str)
            if not (0 <= sample_num_d_int <= 255):
                messagebox.showerror("Input Error", "Sample Num d must be an integer between 0 and 255.")
                return
        except ValueError:
            messagebox.showerror("Input Error", "Sample Num D must be a valid integer.")
            return
        # --- End of new code for M ---    

        try:
            root.after(0, lambda: log_text.insert(tk.END, "[NVM PGM] Starting NVM Programming for Basic Trim...\n"))
            time.sleep(0.1)

            sample_num = entry_sample_num.get().strip()


            #pwr_on()
            if not power_supply:
                root.after(0, lambda: messagebox.showerror("Connection Error", "Power Supply not connected."))
                return
            try:
                voltage = 7.2
                current = 0.5
                power_supply.write(b'*CLS\n'); time.sleep(0.05)
                power_supply.write(b'*RST\n'); time.sleep(0.05)
                power_supply.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
                power_supply.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
                power_supply.write(b'OUTP ON\n'); time.sleep(0.2)
                root.after(0, lambda: log_text.insert(tk.END, f"[PSU] Power Supply ON. V={voltage}V, I={current}A\n"))
            except Exception as e:
                root.after(0, lambda: messagebox.showerror("Power Supply Error", str(e)))
                return
            time.sleep(1.0)

            i2c_write(0x19, 0x02)
            i2c_write(0x55, 0xA1)
            i2c_write(0x56, 0xA2)
            i2c_write(0x61, 0x01)
            i2c_write(0x62, 0x03)
            i2c_write(0x65, 0x01)
            i2c_write(0x66, 0x01)
            i2c_write(0x41, 0xC0)
            i2c_write(0x42, 0x20)
            i2c_write(0x42, 0x21)
            
            i2c_write(0x3F, 0x01); i2c_write(0x40, best_bgr_code); i2c_write(0x42, 0x25); time.sleep(0.5); i2c_write(0x42, 0x21)
            
            i2c_write(0x3F, 0x02); i2c_write(0x40, best_mclk_code); i2c_write(0x42, 0x25); time.sleep(0.5); i2c_write(0x42, 0x21)
            
            i2c_write(0x3F, 0x03); i2c_write(0x40, best_wclk_code); i2c_write(0x42, 0x25); time.sleep(0.5); i2c_write(0x42, 0x21)
            #i2c_write(0x3F, 0x03); i2c_write(0x40, 32 + best_wclk_code); i2c_write(0x42, 0x25); time.sleep(0.5); i2c_write(0x42, 0x21) #PD 극성 반전
            
            i2c_write(0x3F, 0x06); i2c_write(0x40, 128 + best_tc_code); i2c_write(0x42, 0x25); time.sleep(0.5); i2c_write(0x42, 0x21)
            
            i2c_write(0x3F, 0x04); i2c_write(0x40, best_led1_code); i2c_write(0x42, 0x25); time.sleep(0.5); i2c_write(0x42, 0x21)
            
            i2c_write(0x3F, 0x05); i2c_write(0x40, 64 + best_led2_code); i2c_write(0x42, 0x25); time.sleep(0.5); i2c_write(0x42, 0x21)

            i2c_write(0x3F, 0x07); i2c_write(0x40, 0xC0); i2c_write(0x42, 0x25); time.sleep(0.5); i2c_write(0x42, 0x21)

            i2c_write(0x3F, 0x08); i2c_write(0x40, 0xC0); i2c_write(0x42, 0x25); time.sleep(0.5); i2c_write(0x42, 0x21)

            # Program Sample Month
            log_text.insert(tk.END, f"[NVM] Programming Sample Month {sample_num_m_int} ...\n"); root.update_idletasks()
            i2c_write(0x3F, 10) # ADD
            i2c_write(0x40, sample_num_m_int) # DATA
            i2c_write(0x42, 0x25) # NVM_PGM_ON
            time.sleep(0.5) 
            i2c_write(0x42, 0x21) # NVM_PGM_OFF

            # Program Sample Date
            log_text.insert(tk.END, f"[NVM] Programming Sample Date {sample_num_d_int} ...\n"); root.update_idletasks()
            i2c_write(0x3F, 11) # ADD
            i2c_write(0x40, sample_num_d_int) # DATA
            i2c_write(0x42, 0x25) # NVM_PGM_ON
            time.sleep(0.5) 
            i2c_write(0x42, 0x21) # NVM_PGM_OFF

            # Program Sample Number
            log_text.insert(tk.END, f"[NVM] Programming Sample Num{sample_num_int} ...\n"); root.update_idletasks()
            i2c_write(0x3F, 12) # ADD
            i2c_write(0x40, sample_num_int) # DATA
            i2c_write(0x42, 0x25) # NVM_PGM_ON
            time.sleep(0.5) 
            i2c_write(0x42, 0x21) # NVM_PGM_OFF

            i2c_write(0x3F, 0x00); i2c_write(0x40, 0x01); i2c_write(0x42, 0x25); time.sleep(0.5); i2c_write(0x42, 0x21)
                       
            time.sleep(0.5) # Wait for programming
            pwr_off()
            root.after(0, lambda: log_text.insert(tk.END, "[NVM PGM] NVM Programming sequence sent.\n"))
        except Exception as e:
            root.after(0, lambda err=e: messagebox.showerror("NVM Program Error", str(err)))
    threading.Thread(target=worker, daemon=True).start()


def run_nvm_program_man():
    """Executes the NVM_PGM_MAN.txt script."""
    log_text.insert(tk.END, "[SCRIPT] Running NVM_PGM_MAN.txt...\n")
    script_thread = threading.Thread(target=execute_script_from_thread, args=("NVM_PGM_MAN.txt",), daemon=True)
    script_thread.start()

# ------------------------------
# Power Supply Control
# ------------------------------
def pwr_on():
    """Turns on the power supply with voltage and current from the GUI entries."""
    if not power_supply:
        messagebox.showerror("Connection Error", "Power Supply not connected.")
        return

    try:
        voltage = float(entry_psu_volt.get())
        current = float(entry_psu_amp.get())

        power_supply.write(b'*CLS\n'); time.sleep(0.05)
        power_supply.write(b'*RST\n'); time.sleep(0.05)
        power_supply.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
        power_supply.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
        power_supply.write(b'OUTP ON\n'); time.sleep(0.2)
        
        log_text.insert(tk.END, f"[PSU] Power Supply ON. V={voltage}V, I={current}A\n")
    except ValueError:
        messagebox.showerror("Input Error", "Voltage and Max Amp must be valid numbers.")
    except Exception as e:
        messagebox.showerror("Power Supply Error", str(e))

def pwr_off():
    """Turns off the power supply."""
    if not power_supply:
        messagebox.showerror("Connection Error", "Power Supply not connected.")
        return

    try:
        power_supply.write(b'OUTP OFF\n'); time.sleep(0.2)
        log_text.insert(tk.END, "[PSU] Power Supply OFF\n")
    except Exception as e:
        messagebox.showerror("Power Supply Error", str(e))

def set_psu_voltage_current(voltage, current):
    """Sets the voltage and current on the power supply."""
    if not power_supply:
        messagebox.showerror("Connection Error", "Power Supply not connected.")
        return

    try:
        power_supply.write(b'*CLS\n'); time.sleep(0.05)
        power_supply.write(b'*RST\n'); time.sleep(0.05)
        power_supply.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
        power_supply.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
        log_text.insert(tk.END, f"[PSU] Voltage set to {voltage} V, Current set to {current} A\n")
    except Exception as e:
        messagebox.showerror("Power Supply Error", str(e))

def get_psu_status():
    """Queries and displays the current status of the power supply."""
    if not power_supply:
        messagebox.showerror("Connection Error", "Power Supply not connected.")
        return

    try:
        power_supply.write(b'OUTP?\n')
        time.sleep(0.1)
        output_state = power_supply.readline().decode().strip()
        
        power_supply.write(b'VOLT?\n')
        time.sleep(0.1)
        voltage = power_supply.readline().decode().strip()
        
        power_supply.write(b'CURR?\n')
        time.sleep(0.1)
        current = power_supply.readline().decode().strip()
        
        log_text.insert(tk.END, f"[PSU STATUS] Output: {output_state}, Voltage: {voltage} V, Current: {current} A\n")
    except Exception as e:
        messagebox.showerror("Power Supply Error", str(e))

if __name__ == "__main__":
    # ------------------------------
    # Main Application Window
    # ------------------------------
    root = tk.Tk()
    root.title("TRIM Control v0.9")
    
    # ------------------------------
    # Initialize GUI Variables
    # ------------------------------
    com_ports_list = scan_com_ports()
    com_arduino_var = StringVar(root, value="COM13")
    com_dmm_var = StringVar(root, value="COM5")
    com_dmm2_var = StringVar(root, value="COM11")
    com_psu_var = StringVar(root, value="COM12")
    
    bgr_result_var = StringVar(root, value="N/A")
    mclk_result_var = StringVar(root, value="N/A")
    wclk_result_var = StringVar(root, value="N/A")
    led1_result_var = StringVar(root, value="N/A")
    led2_result_var = StringVar(root, value="N/A")
    tc_min_result_var = StringVar(root, value="N/A")

    bgr_trim_code_var = StringVar(root, value="N/A")
    mclk_trim_code_var = StringVar(root, value="N/A")
    wclk_trim_code_var = StringVar(root, value="N/A")
    led1_trim_code_var = StringVar(root, value="N/A")
    led2_trim_code_var = StringVar(root, value="N/A")
    tc_trim_code_var = StringVar(root, value="N/A")
    tc_m2_trim_code_var = StringVar(root, value="N/A")

    # ------------------------------
    # Create Main Frames
    # ------------------------------
    main_frame = tk.Frame(root, padx=5, pady=5)
    main_frame.pack(fill="both", expand=True)
    
    top_frame = tk.Frame(main_frame)
    top_frame.pack(fill="x")
    
    bottom_frame = tk.Frame(main_frame, pady=5)
    bottom_frame.pack(fill="both", expand=True)
    
    left_frame = tk.Frame(top_frame)
    left_frame.pack(side="left", fill="y", padx=(0,5))
    
    right_frame = tk.Frame(top_frame)
    right_frame.pack(side="left", fill="both", expand=True)

    # ------------------------------
    # Left Frame: Connections & Controls
    # ------------------------------
    frame_com = tk.LabelFrame(left_frame, text="Device Connection")
    frame_com.pack(fill="x", pady=(0, 5))

    frame_manual_ctrl = tk.LabelFrame(left_frame, text="I2C & Manual Control")
    frame_manual_ctrl.pack(fill="x")

    # --- COM Port Setup ---
    tk.Label(frame_com, text="Arduino").grid(row=0, column=0, sticky="w", pady=2)
    tk.OptionMenu(frame_com, com_arduino_var, *com_ports_list).grid(row=0, column=1, columnspan=2, sticky="ew", padx=5)
    btn_frame_arduino = tk.Frame(frame_com)
    btn_frame_arduino.grid(row=0, column=3, padx=(10,0), pady=2)
    tk.Button(btn_frame_arduino, text="Open", command=open_arduino).pack(side="left", padx=(0,5))
    tk.Button(btn_frame_arduino, text="Close", command=close_arduino).pack(side="left")

    tk.Label(frame_com, text="Multimeter").grid(row=1, column=0, sticky="w", pady=2)
    tk.OptionMenu(frame_com, com_dmm_var, *com_ports_list).grid(row=1, column=1, columnspan=2, sticky="ew", padx=5)
    btn_frame_dmm = tk.Frame(frame_com)
    btn_frame_dmm.grid(row=1, column=3, padx=(10,0), pady=2)
    tk.Button(btn_frame_dmm, text="Open", command=open_multimeter).pack(side="left", padx=(0,5))
    tk.Button(btn_frame_dmm, text="Close", command=close_multimeter).pack(side="left")

    tk.Label(frame_com, text="Multimeter 2").grid(row=2, column=0, sticky="w", pady=2)
    tk.OptionMenu(frame_com, com_dmm2_var, *com_ports_list).grid(row=2, column=1, columnspan=2, sticky="ew", padx=5)
    btn_frame_dmm2 = tk.Frame(frame_com)
    btn_frame_dmm2.grid(row=2, column=3, padx=(10,0), pady=2)
    tk.Button(btn_frame_dmm2, text="Open", command=open_multimeter2).pack(side="left", padx=(0,5))
    tk.Button(btn_frame_dmm2, text="Close", command=close_multimeter2).pack(side="left")
    
    tk.Label(frame_com, text="Power Supply").grid(row=3, column=0, sticky="w", pady=2)
    tk.OptionMenu(frame_com, com_psu_var, *com_ports_list).grid(row=3, column=1, columnspan=2, sticky="ew", padx=5)
    btn_frame_psu = tk.Frame(frame_com)
    btn_frame_psu.grid(row=3, column=3, padx=(10,0), pady=2)
    tk.Button(btn_frame_psu, text="Open", command=open_psu).pack(side="left", padx=(0,5))
    tk.Button(btn_frame_psu, text="Close", command=close_psu).pack(side="left")

    frame_relay = tk.LabelFrame(frame_com, text="Relay Control")
    frame_relay.grid(row=0, column=4, rowspan=4, sticky="ns", padx=(15, 5), pady=2)
    for i in range(4):
        relay_num = i + 1
        tk.Label(frame_relay, text=f"Relay {relay_num}").grid(row=i, column=0, sticky="w", padx=5, pady=1)
        btn_frame_relay_inner = tk.Frame(frame_relay)
        btn_frame_relay_inner.grid(row=i, column=1)
        tk.Button(btn_frame_relay_inner, text="ON", width=4, command=lambda rn=relay_num: control_arduino_relay(rn, "ON")).pack(side="left")
        tk.Button(btn_frame_relay_inner, text="OFF", width=4, command=lambda rn=relay_num: control_arduino_relay(rn, "OFF")).pack(side="left", padx=(2,0))

    tk.Button(frame_com, text="Connect Nordic MCU", command=lambda: Nordic_mcu.connect_pipe_server()).grid(row=4, column=0, columnspan=2, pady=(10,2), sticky="ew")
    tk.Button(frame_com, text="Disconnect", command=lambda: Nordic_mcu.close()).grid(row=4, column=2, columnspan=2, pady=(10,2), sticky="ew")

    # --- Manual Controls ---
    i2c_frame = tk.Frame(frame_manual_ctrl)
    i2c_frame.pack(fill="x", padx=5, pady=5)
    tk.Label(i2c_frame, text="ADDR").pack(side="left")
    entry_i2c_reg = tk.Entry(i2c_frame, width=8)
    entry_i2c_reg.pack(side="left", padx=5)
    tk.Label(i2c_frame, text="DATA").pack(side="left")
    entry_i2c_data = tk.Entry(i2c_frame, width=8)
    entry_i2c_data.pack(side="left", padx=5)
    tk.Button(i2c_frame, text="Read", command=read_i2c).pack(side="left", padx=(10,0))
    tk.Button(i2c_frame, text="Write", command=write_i2c).pack(side="left", padx=5)

    pulse_frame = tk.Frame(frame_manual_ctrl)
    pulse_frame.pack(fill="x", padx=5, pady=5)
    tk.Label(pulse_frame, text="Pulse Count").pack(side="left")
    entry_pulse_count = tk.Entry(pulse_frame, width=8)
    entry_pulse_count.pack(side="left", padx=5)
    tk.Button(pulse_frame, text="Send Pulse", command=send_pulse_command).pack(side="left", padx=5)

    dmm_measure_frame = tk.LabelFrame(frame_manual_ctrl, text="DMM Measurement")
    dmm_measure_frame.pack(fill="x", padx=5, pady=5)
    tk.Button(dmm_measure_frame, text="V", command=measure_voltage).pack(side="left", expand=True, fill="x")
    tk.Button(dmm_measure_frame, text="A", command=measure_current).pack(side="left", expand=True, fill="x")
    tk.Button(dmm_measure_frame, text="Hz", command=measure_freq).pack(side="left", expand=True, fill="x")
    tk.Button(dmm_measure_frame, text="V2", command=measure_voltage2).pack(side="left", expand=True, fill="x")
    tk.Button(dmm_measure_frame, text="A2", command=measure_current2).pack(side="left", expand=True, fill="x")
    tk.Button(dmm_measure_frame, text="Hz2", command=measure_freq2).pack(side="left", expand=True, fill="x")

    psu_control_frame = tk.LabelFrame(frame_manual_ctrl, text="PSU Control")
    psu_control_frame.pack(fill="x", padx=5, pady=5)
    
    tk.Label(psu_control_frame, text="Voltage").grid(row=0, column=0, padx=(0, 2))
    entry_psu_volt = tk.Entry(psu_control_frame, width=8)
    entry_psu_volt.grid(row=0, column=1)
    tk.Label(psu_control_frame, text="Max Amp").grid(row=0, column=2, padx=(5, 2))
    entry_psu_amp = tk.Entry(psu_control_frame, width=8)
    entry_psu_amp.grid(row=0, column=3)
    tk.Button(psu_control_frame, text="ON", command=pwr_on).grid(row=0, column=4, padx=5)
    tk.Button(psu_control_frame, text="OFF", command=pwr_off).grid(row=0, column=5, padx=5)
    tk.Button(psu_control_frame, text="Status", command=get_psu_status).grid(row=0, column=6, padx=5)
    trim_script_frame = tk.LabelFrame(frame_manual_ctrl, text="Trim Scripts (for Manual Measurement)")
    trim_script_frame.pack(fill="x", padx=5, pady=5)
    tk.Button(trim_script_frame, text="BGR", command=lambda: run_trim_script("bgr")).pack(side="left", expand=True, fill="x")
    tk.Button(trim_script_frame, text="MCLK", command=lambda: run_trim_script("mclk")).pack(side="left", expand=True, fill="x")
    tk.Button(trim_script_frame, text="WCLK", command=lambda: run_trim_script("wclk")).pack(side="left", expand=True, fill="x")
    tk.Button(trim_script_frame, text="LED1", command=lambda: run_trim_script("led1")).pack(side="left", expand=True, fill="x")
    tk.Button(trim_script_frame, text="LED2", command=lambda: run_trim_script("led2")).pack(side="left", expand=True, fill="x")
    tk.Button(trim_script_frame, text="TC", command=lambda: run_trim_script("tc")).pack(side="left", expand=True, fill="x")
    run_all_searches_button = tk.Button(trim_script_frame, text="Run All (BGR->LED2)", command=run_all_trim_scripts)
    run_all_searches_button.pack(side="left", expand=True, fill="x", padx=(5,0))
    

    # ------------------------------
    # Right Frame: Automated Search & NVM
    # ------------------------------
    frame_auto_search = tk.LabelFrame(right_frame, text="Automated Search")
    frame_auto_search.pack(fill="both", expand=True)

    frame_nvm = tk.LabelFrame(right_frame, text="NVM Programming")
    frame_nvm.pack(fill="x", pady=(5,0))

    # --- Sample Number ---
    sample_frame = tk.Frame(frame_auto_search)
    sample_frame.pack(fill="x", padx=5, pady=(5,10))
    tk.Label(sample_frame, text="IC Sample Num:").pack(side="left")
    entry_sample_num = tk.Entry(sample_frame, width=15)
    entry_sample_num.pack(side="left", padx=5)
    tk.Label(sample_frame, text="Sample Month:").pack(side="left", padx=(10,2))
    entry_sample_num_m = tk.Entry(sample_frame, width=6)
    entry_sample_num_m.pack(side="left", padx=(0,5))
    tk.Label(sample_frame, text="Sample Date:").pack(side="left", padx=(10,2))
    entry_sample_num_d = tk.Entry(sample_frame, width=6)
    entry_sample_num_d.pack(side="left", padx=(0,5))
    tk.Button(sample_frame, text="Read from IC", command=run_rd_sample_num).pack(side="left", padx=5)
    
    # --- Search Sections ---
    header_frame = tk.Frame(frame_auto_search)
    header_frame.pack(fill="x", padx=5, pady=(20,0))
    tk.Label(header_frame, text="Item", font=('Helvetica', 9, 'bold'), width=10, anchor="w").pack(side="left")
    tk.Label(header_frame, text="Search Result", font=('Helvetica', 9, 'bold'), anchor="w").pack(side="left", padx=5, fill="x", expand=True)
    tk.Label(header_frame, text="Trim Code", font=('Helvetica', 9, 'bold'), anchor="w").pack(side="left", padx=5, fill="x", expand=True)
    tk.Label(header_frame, text="Action", font=('Helvetica', 9, 'bold'), anchor="w").pack(side="left", padx=5)

    def create_search_row(parent, label, result_var, code_var, command):
        frame = tk.Frame(parent)
        frame.pack(fill="x", padx=5, pady=1)
        tk.Label(frame, text=label, width=10, anchor="w").pack(side="left")
        tk.Label(frame, textvariable=result_var, relief="sunken", width=15, anchor="w").pack(side="left", padx=5, fill="x", expand=True)
        tk.Label(frame, textvariable=code_var, relief="sunken", width=15, anchor="w").pack(side="left", padx=5, fill="x", expand=True)
        button = tk.Button(frame, text=f"{label} Search", command=command)
        button.pack(side="left", padx=5)
        return button

    bgr_search_button = create_search_row(frame_auto_search, "BGR", bgr_result_var, bgr_trim_code_var, run_bgr_search)
    mclk_search_button = create_search_row(frame_auto_search, "MCLK", mclk_result_var, mclk_trim_code_var, run_mclk_search)
    wclk_search_button = create_search_row(frame_auto_search, "WCLK", wclk_result_var, wclk_trim_code_var, run_wclk_search)
    led1_search_button = create_search_row(frame_auto_search, "LED1", led1_result_var, led1_trim_code_var, run_led1_search)
    led2_search_button = create_search_row(frame_auto_search, "LED2", led2_result_var, led2_trim_code_var, run_led2_search)
    tc_search_button = create_search_row(frame_auto_search, "TC", tc_min_result_var, tc_trim_code_var, run_tc_search)
    tc_search_m2_button = tk.Button(frame_auto_search, text="TC Search method 2", command=run_tc_search_m2)
    tc_search_m2_button.pack(fill="x", padx=5, pady=(0,2))

    run_all_searches_button = tk.Button(frame_auto_search, text="Run ALL Searches", command=run_all_searches)
    run_all_searches_button.pack(fill="x", padx=5, pady=(10,2))

    # --- NVM Programming ---
    tk.Button(frame_nvm, text="NVM Program ", command=run_nvm_program).pack(side="left", expand=True, fill="x", padx=2, pady=2)

    tk.Button(frame_nvm, text="Run NVM_PGM_MAN Script", command=run_nvm_program_man).pack(side="left", expand=True, fill="x", padx=2, pady=2)
    run_all_searches_button2 = tk.Button(frame_nvm, text="Run ALL Searches & PGM ", command=run_all_searches2)
    run_all_searches_button2.pack(side="left", expand=True, fill="x", padx=2, pady=2)

    # ------------------------------
    # Bottom Frame: Console + Log
    # ------------------------------
    bottom_split = tk.Frame(bottom_frame)
    bottom_split.pack(fill="both", expand=True)

    console_frame = tk.LabelFrame(bottom_split, text="Console Input")
    console_frame.pack(side="left", fill="both", expand=True, padx=(0,5))
    console_button_frame = tk.Frame(console_frame)
    console_button_frame.pack(side="left", fill="y", padx=5, pady=5)
    tk.Button(console_button_frame, text="Send", command=run_console_cmd).pack(fill="x")
    tk.Button(console_button_frame, text="Clear Input", command=clear_console_input).pack(fill="x", pady=(5,0))
    tk.Button(console_button_frame, text="Clear Log", command=clear_log).pack(fill="x", pady=(5,5))
    console_text_input = scrolledtext.ScrolledText(console_frame, wrap=tk.WORD, width=40, height=15)
    console_text_input.pack(side="left", fill="both", expand=True, padx=5, pady=5)

    log_frame = tk.LabelFrame(bottom_split, text="Log")
    log_frame.pack(side="left", fill="both", expand=True)
    log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=15)
    log_text.pack(fill="both", expand=True, padx=5, pady=5)

    # ------------------------------
    # Initialize Pipe Server and Start Main Loop
    # ------------------------------
    Nordic_mcu = PipeServer(root) # Re-initialize with the correct class
    root.mainloop()
