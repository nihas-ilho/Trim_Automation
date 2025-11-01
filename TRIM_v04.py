# 표준 라이브러리
import time
import tkinter as tk
from tkinter import scrolledtext, messagebox, StringVar
import os
import threading

# 서드파티 라이브러리
import serial
import serial.tools.list_ports

# ------------------------------
# Global Variables
# ------------------------------
arduino = None
multimeter = None
power_supply = None # Power supply object
last_script_context = None # To track which TRIM button was last pressed
I2C_ADDR = 0x39 # Fixed I2C device address
best_bgr_code = -1 # To store the result from BGR Search
best_mclk_code = -1 # To store the result from MCLK Search
best_wclk_code = -1 # To store the result from WCLK Search
best_led1_code = -1 # To store the result from LED1 Search
best_led2_code = -1 # To store the result from LED2 Search
best_tc_code = -1 # To store the result from TC Search

# ------------------------------
# I2C Control Functions
# ------------------------------
def i2c_write(reg, data):
    if not arduino: return "Arduino not connected"
    arduino.reset_input_buffer() # Clear input buffer before writing
    cmd = f"W,{I2C_ADDR:#04x},{reg:#04x},{data:#04x}\n"
    arduino.write(cmd.encode())
    resp = arduino.readline().decode().strip()
    return resp

def i2c_read(reg):
    if not arduino: return "Arduino not connected"
    arduino.reset_input_buffer() # Clear input buffer before reading
    cmd = f"R,{I2C_ADDR:#04x},{reg:#04x}\n"
    arduino.write(cmd.encode())
    resp = arduino.readline().decode().strip()
    if resp.startswith("DATA"):
        _, val = resp.split(",")
        return int(val, 16)
    return None

# ------------------------------
# BGR Trim Code Calculation
# ------------------------------
def calculate_bgr_trim_code(measured_voltage):
    """Calculates the BGR trim code and operation based on the measured voltage."""
    try:
        delta = 1.222 - float(measured_voltage)

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
def update_result_display(raw_value, unit):
    """Parses the last value from the instrument, formats it, and displays it."""
    global last_script_context
    if not last_script_context: # Do nothing if no TRIM script was run
        return

    try:
        if ',' in raw_value:
            last_value_str = raw_value.split(',')[-1]
        else:
            last_value_str = raw_value
        
        numeric_value = float(last_value_str)
        
        display_unit = unit
        if last_script_context in ["wclk", "mclk"] and unit == "Hz":
            numeric_value /= 1000
            display_unit = "kHz"
        elif last_script_context in ["led1", "led2", "tc"] and unit == "A":
            numeric_value *= 1000
            display_unit = "mA"

        formatted_result = f"{numeric_value:.3f} {display_unit}"
        
        if last_script_context == "bgr":
            bgr_result_var.set(formatted_result)
            # Only calculate trim code for voltage measurements
            if unit == "V":
                trim_code = calculate_bgr_trim_code(numeric_value)
                bgr_trim_code_var.set(trim_code)
        elif last_script_context == "wclk":
            wclk_result_var.set(formatted_result)
        elif last_script_context == "mclk":
            mclk_result_var.set(formatted_result)
        elif last_script_context == "led1":
            led1_result_var.set(formatted_result)
        elif last_script_context == "led2":
            led2_result_var.set(formatted_result)
        elif last_script_context == "tc":
            tc_min_result_var.set(formatted_result)
            
    except (ValueError, IndexError):
        error_msg = "Parse Err"
        if last_script_context == "bgr":
            bgr_result_var.set(error_msg)
            bgr_trim_code_var.set("Error")
        elif last_script_context == "wclk":
            wclk_result_var.set(error_msg)
        elif last_script_context == "mclk":
            mclk_result_var.set(error_msg)
        elif last_script_context == "led1":
            led1_result_var.set(error_msg)
        elif last_script_context == "led2":
            led2_result_var.set(error_msg)
        elif last_script_context == "tc":
            tc_min_result_var.set(error_msg)


# ------------------------------
# Multimeter Functions
# ------------------------------
def _get_raw_voltage():
    """Internal function to get a raw voltage reading. Returns raw string or None."""
    if not multimeter:
        return None
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
    """Internal function for fast voltage reading, assuming DMM is already configured. Only sends INIT and FETC?."""
    if not multimeter:
        return None
    ser = multimeter
    # The multimeter is already configured, just trigger and fetch
    ser.write(b'INIT\n'); time.sleep(0.2)
    ser.write(b'FETC?\n'); time.sleep(0.2) # This wait is likely for the measurement and transfer
    
    value = ser.readline().decode().strip()
    return value

def _get_fast_voltage2():
    """Internal function for fast voltage reading, assuming DMM is already configured. Only sends INIT and FETC?."""
    if not multimeter2:
        return None
    ser = multimeter2
    # The multimeter is already configured, just trigger and fetch
    ser.write(b'INIT\n'); time.sleep(0.2)
    ser.write(b'FETC?\n'); time.sleep(0.2) # This wait is likely for the measurement and transfer
    
    value = ser.readline().decode().strip()
    return value

def measure_voltage():
    raw_value = _get_raw_voltage()
    if raw_value is None:
        log_text.insert(tk.END, "[ERROR] Multimeter not connected\n")
        return
    
    log_text.insert(tk.END, f"[DMM] Voltage = {raw_value} V\n")
    log_text.see(tk.END)
    update_result_display(raw_value, "V")

def _get_fast_freq():
    """Internal function for fast frequency reading, assuming DMM is already configured."""
    if not multimeter:
        return None
    ser = multimeter
    ser.write(b'INIT\n'); time.sleep(0.4)
    ser.write(b'FETC?\n'); time.sleep(0.4)
    
    value = ser.readline().decode().strip()
    return value

def measure_freq():
    if not multimeter:
        log_text.insert(tk.END, "[ERROR] Multimeter not connected\n")
        return
    ser = multimeter
    ser.write(b'*CLS\n'); time.sleep(0.05)
    ser.write(b'CONF:FREQ\n'); time.sleep(0.05)
    ser.write(b'FREQ:APER 0.1\n'); time.sleep(0.05)
    ser.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)
    ser.write(b'INIT\n'); time.sleep(0.55)
    ser.write(b'FETC?\n'); time.sleep(0.55)

    value = ser.readline().decode().strip()
    ser.write(b'*CLS\n')
    
    log_text.insert(tk.END, f"[DMM] Frequency = {value} Hz\n")
    log_text.see(tk.END)
    update_result_display(value, "Hz")

def _get_fast_current():
    """Internal function for fast current reading, assuming DMM is already configured."""
    if not multimeter:
        return None
    ser = multimeter
    # The multimeter is already configured, just trigger and fetch
    ser.write(b'INIT\n'); time.sleep(0.2)
    ser.write(b'FETC?\n'); time.sleep(0.7) # Increased wait time for measurement and transfer
    
    value = ser.readline().decode().strip()
    return value

def measure_current():
    if not multimeter:
        log_text.insert(tk.END, "[ERROR] Multimeter not connected\n")
        return
    ser = multimeter
    ser.write(b'*CLS\n'); time.sleep(0.05)
    # Configure for DC Current measurement, auto-range
    ser.write(b'CONF:CURR:DC\n'); time.sleep(0.05)
    ser.write(b'CURR:DC:NPLC 1\n'); time.sleep(0.05)
    ser.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)
    ser.write(b'INIT\n'); time.sleep(0.2)
    ser.write(b'FETC?\n'); time.sleep(0.55)

    value = ser.readline().decode().strip()
    ser.write(b'*CLS\n')
    
    log_text.insert(tk.END, f"[DMM] Current = {value} A\n")
    log_text.see(tk.END)
    update_result_display(value, "A")

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
    try:
        multimeter = serial.Serial(com_dmm_var.get(), 9600, timeout=1)
        log_text.insert(tk.END, f"[INFO] Multimeter connected on {com_dmm_var.get()}\n")
    except Exception as e:
        messagebox.showerror("Error", str(e))

def open_multimeter2():
    global multimeter2
    try:
        multimeter2 = serial.Serial(com_dmm2_var.get(), 9600, timeout=1)
        log_text.insert(tk.END, f"[INFO] Multimeter 2 connected on {com_dmm2_var.get()}\n")
    except Exception as e:
        messagebox.showerror("Error", str(e))

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
        else:
            log_text.insert(tk.END, f"[I2C READ] Reg=0x{reg:02X} -> Read failed. No data received.\n")
        
        log_text.see(tk.END)
    except ValueError:
        messagebox.showerror("Input Error", "ADDRESS must be a valid hexadecimal number.")
    except Exception as e:
        messagebox.showerror("Error", str(e))

def send_pulse_command():
    """Sends the command to generate a specific number of pulses to the Arduino."""
    if not arduino:
        messagebox.showerror("Connection Error", "Arduino not connected.")
        return
    try:
        count_str = entry_pulse_count.get()
        if not count_str:
            messagebox.showerror("Input Error", "Pulse count cannot be empty.")
            return
        
        count = int(count_str)
        if not (0 < count <= 9000):
            messagebox.showerror("Input Error", "Pulse count must be between 1 and 9000.")
            return

        cmd = f"t,{count}\n" # P -> t
        arduino.write(cmd.encode())
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

def run_rd_sample_num():
    """
    Reads the pre-programmed sample number from the IC and populates
    the 'IC Sample Num' entry field.
    """
    if not arduino:
        messagebox.showerror("Connection Error", "Arduino not connected.")
        return

    try:
        log_text.insert(tk.END, "[CMD] Reading Sample Number...\n")
        log_text.see(tk.END)

        # 1. W,0x19,0x02 (Software Reset)
        i2c_write(0x19, 0x02)
        log_text.insert(tk.END, "  W,0x19,0x02 (SW Reset)\n")
        
        # 2. D,2 (Delay 2ms)
        time.sleep(2 / 1000.0)
        
        # 3. W,0x55,0xA1 (TEST_KEY)
        i2c_write(0x55, 0xA1)
        log_text.insert(tk.END, "  W,0x55,0xA1 (TEST_KEY)\n")

        # 4. W,0x56,0xA2 (NVM_KEY)
        i2c_write(0x56, 0xA2)
        log_text.insert(tk.END, "  W,0x56,0xA2 (NVM_KEY)\n")

        # 5. R,0x00 (Read chipID)
        chip_id = i2c_read(0x00)
        if chip_id is not None:
            log_text.insert(tk.END, f"  R,0x00 (Chip ID) -> 0x{chip_id:02X}\n")
        else:
            log_text.insert(tk.END, "  R,0x00 (Chip ID) -> Read Failed\n")

        # 6. R,0x51 (Read SampleNum)
        sample_num_val = i2c_read(0x51)
        
        if sample_num_val is not None:
            log_text.insert(tk.END, f"  R,0x51 (Sample Num) -> 0x{sample_num_val:02X} ({sample_num_val})\n")
            
            # 7. Update the GUI
            entry_sample_num.delete(0, tk.END) # Clear existing content
            entry_sample_num.insert(0, str(sample_num_val)) # Insert the new value
            log_text.insert(tk.END, f"[INFO] 'IC Sample Num' field updated to: {sample_num_val}\n")
        else:
            log_text.insert(tk.END, "  R,0x51 (Sample Num) -> Read Failed\n")
            messagebox.showwarning("Read Error", "Failed to read Sample Number from 0x51.")

        log_text.see(tk.END)

    except Exception as e:
        messagebox.showerror("Error", str(e))
        log_text.insert(tk.END, f"[ERROR] {str(e)}\n")
        log_text.see(tk.END)

# ------------------------------
# Automated Search and NVM Programming
# ------------------------------
def execute_script_from_thread(filename, file_handle=None):
    """Helper to run script commands from a thread, logging to GUI and optionally a file."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)
        with open(file_path, 'r') as f:
            script_content = f.read()
        
        cmds = script_content.strip().splitlines()
        for cmd_line in cmds:
            # This is a simplified version of run_console_cmd for thread safety
            # It directly sends commands and logs, assuming correct format
            parts = cmd_line.split("//")[0].strip().split(",")
            cmd = parts[0].upper()
            
            log_line = f"> {cmd_line}\n"
            def update_log(line):
                log_text.insert(tk.END, line)
                log_text.see(tk.END)
                if file_handle: file_handle.write(line)

            root.after(0, lambda l=log_line: update_log(l))
            
            # Execute command
            if cmd == "W":
                reg, data = int(parts[1], 16), int(parts[2], 16)
                i2c_write(reg, data)
            elif cmd == "D":
                delay_ms = int(parts[1])
                time.sleep(delay_ms / 1000.0)
            # Add other simple commands if needed, but avoid complex GUI interactions
            time.sleep(0.05) # Small delay between script commands
    except Exception as e:
        root.after(0, lambda: messagebox.showerror("Script Error", f"Error executing {filename}:\n{e}"))


def bgr_search_worker(sample_num):
    """The actual BGR search logic that runs in a separate thread."""
    try:
        global best_bgr_code
        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)
        results = []

        with open(file_path, "w") as f:
            f.write(f"--- IC Sample Num: {sample_num} ---\n\n")
            
            # --- BGR Search ---
            f.write("--- BGR Search Results ---\n")
            execute_script_from_thread("TRIM_BGR.txt", f)

            if multimeter:
                ser = multimeter
                ser.write(b'*CLS\n'); time.sleep(0.05)
                ser.write(b'CONF:VOLT:DC 10\n'); time.sleep(0.05)
                ser.write(b'VOLT:DC:RANG 10\n'); time.sleep(0.05)
                ser.write(b'VOLT:DC:NPLC 1\n'); time.sleep(0.05)
                ser.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)

            for i in range(0, 128):
                i2c_write(0x3F, 0x01); i2c_write(0x40, i); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                raw_value = _get_fast_voltage()
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
                
                def update_gui_and_file(line_to_log):
                    log_text.insert(tk.END, line_to_log)
                    log_text.see(tk.END)
                    f.write(line_to_log)
                root.after(0, lambda line=log_line: update_gui_and_file(line))

            best_code, best_voltage, analysis_result = -1, 0.0, ""
            if not results:
                analysis_result = "[ANALYSIS] No valid BGR data collected.\n"
            else:
                target_voltage = 1.22
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

def mclk_search_worker(sample_num):
    """The actual MCLK search logic that runs in a separate thread."""
    try:
        global best_mclk_code
        #if best_bgr_code == -1:
        #    root.after(0, lambda: messagebox.showerror("Prerequisite Error", "Run BGR Search first."))
        #    root.after(0, lambda: mclk_search_button.config(state=tk.NORMAL))
        #    return

        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)
        results = []
        TARGET_MCLK_HZ = 312500.0

        with open(file_path, "a") as f:
            f.write("\n--- MCLK Search Results ---\n")
            execute_script_from_thread("TRIM_MCLK.txt", f)
            
            # Apply best BGR code
            i2c_write(0x3F, 0x01); i2c_write(0x40, best_bgr_code); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)

            if multimeter:
                ser = multimeter
                ser.write(b'*CLS\n'); time.sleep(0.05)
                ser.write(b'CONF:FREQ\n'); time.sleep(0.05)
                ser.write(b'FREQ:APER 0.1\n'); time.sleep(0.05)
                ser.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)

            for i in range(0, 128):
                i2c_write(0x3F, 0x02); i2c_write(0x40, i); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                raw_value = _get_fast_freq()
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
                
                def update_gui_and_file(line_to_log):
                    log_text.insert(tk.END, line_to_log)
                    log_text.see(tk.END)
                    f.write(line_to_log)
                root.after(0, lambda line=log_line: update_gui_and_file(line))
            
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
        root.after(0, lambda: messagebox.showerror("MCLK Search Error", str(e)))
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
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)
        results = []
        TARGET_WCLK_HZ = 10000.0

        with open(file_path, "a") as f:
            f.write("\n--- WCLK Search Results ---\n")
            execute_script_from_thread("TRIM_WCLK.txt", f)
            
            i2c_write(0x3F, 0x01); i2c_write(0x40, best_bgr_code); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)

            if multimeter:
                ser = multimeter
                ser.write(b'*CLS\n'); time.sleep(0.05)
                ser.write(b'CONF:FREQ\n'); time.sleep(0.05)
                ser.write(b'FREQ:APER 0.1\n'); time.sleep(0.05)
                ser.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)

            for i in range(32):
                i2c_write(0x3F, 0x03); i2c_write(0x40, i); i2c_write(0x41, 0x10); i2c_write(0x41, 0x00)
                raw_value = _get_fast_freq()
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
                
                def update_gui_and_file(line_to_log):
                    log_text.insert(tk.END, line_to_log)
                    log_text.see(tk.END)
                    f.write(line_to_log)
                root.after(0, lambda line=log_line: update_gui_and_file(line))

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
        root.after(0, lambda: messagebox.showerror("WCLK Search Error", str(e)))
        root.after(0, lambda: wclk_search_button.config(state=tk.NORMAL))

def led1_search_worker(sample_num):
    """The actual LED1 search logic that runs in a separate thread."""
    try:
        global best_led1_code
        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)
        results = []
        TARGET_CURRENT_A = 0.0618 # 61.8mA

        with open(file_path, "a") as f:
            f.write("\n--- LED1 Search Results ---\n")

            # 1. Power Supply ON
            voltage = 0.8
            current = 0.5
            log_text.insert(tk.END, f"[PSU] Setting ON. V={voltage}V, I={current}A\n")
            
            ser = power_supply
            ser.write(b'*CLS\n'); time.sleep(0.05)
            ser.write(b'*RST\n'); time.sleep(0.05)
            ser.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
            ser.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
            ser.write(b'OUTP ON\n'); time.sleep(0.2)
            time.sleep(2.0)

            log_text.insert(tk.END, "[PSU] Output is ON\n")
            root.update_idletasks()

            # Configure multimeter for fast current readings
            if multimeter:
                ser = multimeter
                ser.write(b'*CLS\n'); time.sleep(0.05)
                ser.write(b'CONF:CURR:DC\n'); time.sleep(0.05)
                ser.write(b'CURR:DC:NPLC 1\n'); time.sleep(0.05)
                ser.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)

            # Iterate through the codes 16 to 48
            for i in range(0, 64): # range is exclusive of the end, so 49
                
                def log_sequence_start(code):
                    #log_text.insert(tk.END, f"--- Running sequence for LED1 code {code} ---\n")
                    log_text.see(tk.END)
                root.after(0, lambda c=i: log_sequence_start(c))
                
                # --- Start of sequence from TRIM_LED1.txt ---
                i2c_write(0x19, 0x02) # Software Reset
                time.sleep(10 / 1000.0)
                i2c_write(0x55, 0xA1) # TEST_KEY
                i2c_write(0x56, 0xA2) # NVM_KEY
                i2c_write(0x02, 0x00) # OP Mode Off, Temp Off, Seq2 Off
                i2c_write(0x3A, 0x31) # I2C_SPR_EN On, EOC_SEL On
                i2c_write(0x06, 0x7F) # Pulse Width 56us
                i2c_write(0x1D, 0xFF) # LED1 Current Sel
                i2c_write(0x65, 0x01) # OSC testmode
                i2c_write(0x66, 0x01) # OSC MCLK_EN
                i2c_write(0x5C, 0x01) # ALC testmode
                i2c_write(0x5D, 0xBF) # ALC DAC = 11111111111
                i2c_write(0x5E, 0x1F)

                # Set LED1_TRIM code using the loop variable 'i'
                i2c_write(0x3F, 0x04) # NVM_ADDR 4
                i2c_write(0x40, i)    # Set LED1_TRIM[5:0]
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                i2c_write(0x3C, 0x8B) # INT_MAN, RSTB_MAN, RSTB OEN=1, IE=1
                i2c_write(0x6E, 0x43) # INT=ALC_FIND, RSTB=CLK_IN
                i2c_write(0x01, 0x01) # S_SENSE ON
                time.sleep(10 / 1000.0)
                
                # Send pulse command
                if arduino:
                    arduino.write(b"t,3200\n")
                
                # Wait for 1 second
                time.sleep(1.5)

                # Measure current
                raw_value = _get_fast_current()
                
                log_line = ""
                if raw_value:
                    try:
                        # Convert to float and handle scientific notation
                        #numeric_value = float(raw_value)
                        numeric_value = float(raw_value.split(',')[-1]) 
                        results.append({'code': i, 'current': numeric_value})
                        log_line = f"LED1 CODE {i}, {numeric_value * 1000:.3f} mA\n"
                    except (ValueError, IndexError):
                        log_line = f"LED1 CODE {i}, Parse Error on value: '{raw_value}'\n"
                else:
                    # This case handles if _get_fast_current returns None or empty string
                    log_line = f"LED1 CODE {i}, DMM read failed (Timeout or No Data)\n"
                
                def update_gui_and_file(line_to_log):
                    log_text.insert(tk.END, line_to_log)
                    log_text.see(tk.END)
                    f.write(line_to_log)
                root.after(0, lambda line=log_line: update_gui_and_file(line))

            # Analysis of results
            best_code, best_current, analysis_result = -1, 0.0, ""
            if not results:
                analysis_result = "[ANALYSIS] No valid LED1 data collected.\n"
            else:
                min_diff = float('inf')
                for res in results:
                    diff = abs(res['current'] - TARGET_CURRENT_A)
                    if diff < min_diff:
                        min_diff = diff
                        best_code = res['code']
                        best_current = res['current']
                best_led1_code = best_code
                analysis_result = (
                    f"\n[ANALYSIS] Target: {TARGET_CURRENT_A * 1000:.1f} mA\n"
                    f"[ANALYSIS] Best LED1 code found: {best_code} (0x{best_code:02X}) with Current: {best_current * 1000:.3f} mA\n"
                )
            f.write(analysis_result)

        def final_message(result_text, final_current, final_code):
            log_text.insert(tk.END, f"\n[INFO] LED1 Search complete. Results appended to {filename}\n")
            log_text.insert(tk.END, result_text)
            log_text.see(tk.END)
            led1_search_button.config(state=tk.NORMAL)
            if final_code != -1:
                led1_result_var.set(f"{final_current * 1000:.3f} mA")
                led1_trim_code_var.set(f"{final_code} / 0x{final_code:02X}")
        root.after(0, lambda: final_message(analysis_result, best_current, best_code))

    except Exception as e:
        def error_message():
            messagebox.showerror("LED1 Search Error", str(e))
            led1_search_button.config(state=tk.NORMAL)
        root.after(0, error_message)

def led2_search_worker(sample_num):
    """The actual LED2 search logic that runs in a separate thread."""
    try:
        global best_led2_code
        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)
        results = []
        TARGET_CURRENT_A = 0.0618 # 61.8mA

        with open(file_path, "a") as f:
            f.write("\n--- LED2 Search Results ---\n")

            # 1. Power Supply ON
            voltage = 0.8
            current = 0.5
            log_text.insert(tk.END, f"[PSU] Setting ON. V={voltage}V, I={current}A\n")
            
            ser = power_supply
            ser.write(b'*CLS\n'); time.sleep(0.05)
            ser.write(b'*RST\n'); time.sleep(0.05)
            ser.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
            ser.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
            ser.write(b'OUTP ON\n'); time.sleep(0.2)
            time.sleep(2.0)

            log_text.insert(tk.END, "[PSU] Output is ON\n")
            root.update_idletasks()

            # Configure multimeter for fast current readings
            if multimeter:
                ser = multimeter
                ser.write(b'*CLS\n'); time.sleep(0.05)
                ser.write(b'CONF:CURR:DC\n'); time.sleep(0.05)
                ser.write(b'CURR:DC:NPLC 1\n'); time.sleep(0.05)
                ser.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)

            # Iterate through the codes 16 to 48
            for i in range(0, 64): # range is exclusive of the end, so 49
                
                def log_sequence_start(code):
                    #log_text.insert(tk.END, f"--- Running sequence for LED1 code {code} ---\n")
                    log_text.see(tk.END)
                root.after(0, lambda c=i: log_sequence_start(c))
                
                # --- Start of sequence from TRIM_LED1.txt ---
                i2c_write(0x19, 0x02) # Software Reset
                time.sleep(10 / 1000.0)
                i2c_write(0x55, 0xA1) # TEST_KEY
                i2c_write(0x56, 0xA2) # NVM_KEY
                i2c_write(0x02, 0x01) # OP Mode Off, Temp Off, Seq2 On
                i2c_write(0x3A, 0x31) # I2C_SPR_EN On, EOC_SEL On
                i2c_write(0x06, 0x7F) # Pulse Width 56us
                i2c_write(0x1E, 0xFF) # LED2 Current Sel
                i2c_write(0x65, 0x01) # OSC testmode
                i2c_write(0x66, 0x01) # OSC MCLK_EN
                i2c_write(0x5C, 0x01) # ALC testmode
                i2c_write(0x5D, 0xBF) # ALC DAC = 11111111111
                i2c_write(0x5E, 0x1F)

                # Set LED2_TRIM code using the loop variable 'i'
                i2c_write(0x3F, 0x05) # NVM_ADDR 5
                i2c_write(0x40, 64+i)    # Set LED2_TRIM[5:0] + CLK5M_DIV = 01
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                i2c_write(0x3C, 0x8B) # INT_MAN, RSTB_MAN, RSTB OEN=1, IE=1
                i2c_write(0x6E, 0x43) # INT=ALC_FIND, RSTB=CLK_IN
                i2c_write(0x01, 0x01) # S_SENSE ON
                time.sleep(10 / 1000.0)
                
                # Send pulse command
                if arduino:
                    arduino.write(b"t,5080\n")
                
                # Wait for 1 second
                time.sleep(2.2)

                # Measure current
                raw_value = _get_fast_current()
                
                log_line = ""
                if raw_value:
                    try:
                        # Convert to float and handle scientific notation
                        #numeric_value = float(raw_value)
                        numeric_value = float(raw_value.split(',')[-1]) 
                        results.append({'code': i, 'current': numeric_value})
                        log_line = f"LED2 CODE {i}, {numeric_value * 1000:.3f} mA\n"
                    except (ValueError, IndexError):
                        log_line = f"LED2 CODE {i}, Parse Error on value: '{raw_value}'\n"
                else:
                    # This case handles if _get_fast_current returns None or empty string
                    log_line = f"LED2 CODE {i}, DMM read failed (Timeout or No Data)\n"
                
                def update_gui_and_file(line_to_log):
                    log_text.insert(tk.END, line_to_log)
                    log_text.see(tk.END)
                    f.write(line_to_log)
                root.after(0, lambda line=log_line: update_gui_and_file(line))

            # Analysis of results
            best_code, best_current, analysis_result = -1, 0.0, ""
            if not results:
                analysis_result = "[ANALYSIS] No valid LED2 data collected.\n"
            else:
                min_diff = float('inf')
                for res in results:
                    diff = abs(res['current'] - TARGET_CURRENT_A)
                    if diff < min_diff:
                        min_diff = diff
                        best_code = res['code']
                        best_current = res['current']
                best_led2_code = best_code
                analysis_result = (
                    f"\n[ANALYSIS] Target: {TARGET_CURRENT_A * 1000:.1f} mA\n"
                    f"[ANALYSIS] Best LED2 code found: {best_code} (0x{best_code:02X}) with Current: {best_current * 1000:.3f} mA\n"
                )
            f.write(analysis_result)

        def final_message(result_text, final_current, final_code):
            log_text.insert(tk.END, f"\n[INFO] LED2 Search complete. Results appended to {filename}\n")
            log_text.insert(tk.END, result_text)
            log_text.see(tk.END)
            led2_search_button.config(state=tk.NORMAL)
            if final_code != -1:
                led2_result_var.set(f"{final_current * 1000:.3f} mA")
                led2_trim_code_var.set(f"{final_code} / 0x{final_code:02X}")
        root.after(0, lambda: final_message(analysis_result, best_current, best_code))

    except Exception as e:
        def error_message():
            messagebox.showerror("LED2 Search Error", str(e))
            led2_search_button.config(state=tk.NORMAL)
        root.after(0, error_message)

def tc_search_worker(sample_num, target_current_amps): # <-- Argument added here
    """The actual TC search logic that runs in a separate thread."""
    try:
        global best_tc_code
        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)
        results = []
        
        # Use the passed argument instead of the hard-coded value
        TARGET_CURRENT_A = target_current_amps 

        with open(file_path, "a") as f:
            f.write("\n--- TC Search Results ---\n")

            # 1. Power Supply ON
            voltage = 0.8
            current = 0.5
            log_text.insert(tk.END, f"[PSU] Setting ON. V={voltage}V, I={current}A\n")
            
            ser = power_supply
            ser.write(b'*CLS\n'); time.sleep(0.05)
            ser.write(b'*RST\n'); time.sleep(0.05)
            ser.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
            ser.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
            ser.write(b'OUTP ON\n'); time.sleep(0.2)
            time.sleep(2.0)

            log_text.insert(tk.END, "[PSU] Output is ON\n")
            root.update_idletasks()

            # Configure multimeter for fast current readings
            if multimeter:
                ser = multimeter
                ser.write(b'*CLS\n'); time.sleep(0.05)
                ser.write(b'CONF:CURR:DC\n'); time.sleep(0.05)
                ser.write(b'CURR:DC:NPLC 1\n'); time.sleep(0.05)
                ser.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)

            # Iterate through the codes 0 to 31
            for i in range(0, 32): # range is exclusive of the end, so 32
                
                def log_sequence_start(code):
                    #log_text.insert(tk.END, f"--- Running sequence for TC code {code} ---\n")
                    log_text.see(tk.END)
                root.after(0, lambda c=i: log_sequence_start(c))
                
                # --- Start of sequence from TRIM_TC.txt ---
                i2c_write(0x19, 0x02) # Software Reset
                time.sleep(10 / 1000.0)
                i2c_write(0x55, 0xA1) # TEST_KEY
                i2c_write(0x56, 0xA2) # NVM_KEY
                i2c_write(0x02, 0x00) # OP Mode Off, Temp Off, Seq2 Off
                i2c_write(0x3A, 0x31) # I2C_SPR_EN On, EOC_SEL On
                i2c_write(0x06, 0x7F) # Pulse Width 56us
                i2c_write(0x1D, 0xFF) # LED1 Current Sel 61.8mA
                i2c_write(0x65, 0x01) # OSC testmode
                i2c_write(0x66, 0x01) # OSC MCLK_EN
                i2c_write(0x5C, 0x01) # ALC testmode
                i2c_write(0x5D, 0xBF) # ALC DAC = 11111111111
                i2c_write(0x5E, 0x1F)

                # Set LED1_TRIM, LED2_TRIM
                i2c_write(0x3F, 0x04) # NVM_ADDR 4
                i2c_write(0x40, 0x20) # Set LED1_TRIM[5:0](=10000)
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                i2c_write(0x3F, 0x05) # NVM_ADDR 5
                i2c_write(0x40, 0x60) # Set LED2_TRIM[5:0](=10000) + CLK5M_DIV(=01)
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                # Set TC_TRIM code using the loop variable 'i'
                i2c_write(0x3F, 0x06) # NVM_ADDR 6
                i2c_write(0x40, 64+i) # Set TC_TRIM[4:0] + TC_EN
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                # Set TC_SEL_LED1 = MAX or MIN
                i2c_write(0x3F, 0x07) # NVM_ADDR 7
                #i2c_write(0x40, 0x00) # MIN
                i2c_write(0x40, 0x3F) # MAX
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                i2c_write(0x3C, 0x8B) # INT_MAN, RSTB_MAN, RSTB OEN=1, IE=1
                i2c_write(0x6E, 0x43) # INT=ALC_FIND, RSTB=CLK_IN
                i2c_write(0x01, 0x01) # S_SENSE ON
                #time.sleep(10 / 1000.0)
                
                # Send pulse command
                if arduino:
                    arduino.write(b"t,3200\n")
                
                # Wait for 1 second
                time.sleep(1.5)

                # Measure current
                raw_value = _get_fast_current()
                
                log_line = ""
                if raw_value:
                    try:
                        # Convert to float and handle scientific notation
                        #numeric_value = float(raw_value)
                        numeric_value = float(raw_value.split(',')[-1]) 
                        results.append({'code': i, 'current': numeric_value})
                        log_line = f"TC CODE {i}, {numeric_value * 1000:.3f} mA\n"
                    except (ValueError, IndexError):
                        log_line = f"TC CODE {i}, Parse Error on value: '{raw_value}'\n"
                else:
                    # This case handles if _get_fast_current returns None or empty string
                    log_line = f"TC CODE {i}, DMM read failed (Timeout or No Data)\n"
                
                def update_gui_and_file(line_to_log):
                    log_text.insert(tk.END, line_to_log)
                    log_text.see(tk.END)
                    f.write(line_to_log)
                root.after(0, lambda line=log_line: update_gui_and_file(line))

            # Analysis of results
            best_code, best_current, analysis_result = -1, 0.0, ""
            if not results:
                analysis_result = "[ANALYSIS] No valid TC data collected.\n"
            else:
                min_diff = float('inf')
                for res in results:
                    diff = abs(res['current'] - TARGET_CURRENT_A)
                    if diff < min_diff:
                        min_diff = diff
                        best_code = res['code']
                        best_current = res['current']
                best_tc_code = best_code
                analysis_result = (
                    # This log message also uses the dynamic target
                    f"\n[ANALYSIS] Target: {TARGET_CURRENT_A * 1000:.1f} mA\n"
                    f"[ANALYSIS] Best TC code found: {best_code} (0x{best_code:02X}) with Current: {best_current * 1000:.3f} mA\n"
                )
            f.write(analysis_result)

        def final_message(result_text, final_current, final_code):
            log_text.insert(tk.END, f"\n[INFO] TC Search complete. Results appended to {filename}\n")
            log_text.insert(tk.END, result_text)
            log_text.see(tk.END)
            tc_search_button.config(state=tk.NORMAL)
            if final_code != -1:
                tc_result_var.set(f"{final_current * 1000:.3f} mA")
                tc_trim_code_var.set(f"{final_code} / 0x{final_code:02X}")
        root.after(0, lambda: final_message(analysis_result, best_current, best_code))

    except Exception as e:
        root.after(0, lambda: messagebox.showerror("TC Search Error", str(e)))
        root.after(0, lambda: tc_search_button.config(state=tk.NORMAL))

def tc_search_worker_m2(sample_num, target_current_amps):
    """
    The actual TC search logic (Method 2) that runs in a separate thread.
    This version measures twice and finds the code with the MINIMUM DIFFERENCE
    between the two measurements.
    """
    try:
        global best_tc_code
        filename = f"_Trim_Results_{sample_num}.txt" if sample_num else "_Trim_Results.txt"
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)
        results = []
        
        # This target is not used for finding the best code in this method,
        # but it can be kept for logging or future reference.
        TARGET_CURRENT_A = target_current_amps 

        with open(file_path, "a") as f:
            f.write("\n--- TC Search Results (Method 2 - Minimum Difference) ---\n")

            # 1. Power Supply ON
            voltage = 0.8    
            current = 0.5
            log_text.insert(tk.END, f"[PSU] Setting ON. V={voltage}V, I={current}A\n")
            
            ser = power_supply
            ser.write(b'*CLS\n'); time.sleep(0.05)
            ser.write(b'*RST\n'); time.sleep(0.05)
            ser.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
            ser.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
            ser.write(b'OUTP ON\n'); time.sleep(0.2)
            time.sleep(2.0)

            log_text.insert(tk.END, "[PSU] Output is ON\n")
            root.update_idletasks()

            # Configure multimeter for fast current readings
            if multimeter:
                ser = multimeter
                ser.write(b'*CLS\n'); time.sleep(0.05)
                ser.write(b'CONF:CURR:DC\n'); time.sleep(0.05)
                ser.write(b'CURR:DC:NPLC 1\n'); time.sleep(0.05)
                ser.write(b'SAMPLE:COUNT 1\n'); time.sleep(0.55)

            # Iterate through the codes 0 to 31
            for i in range(0, 32):
                
                def log_sequence_start(code):
                    log_text.insert(tk.END, f"--- Running sequence for TC code {code} ---\n")
                    log_text.see(tk.END)
                root.after(0, lambda c=i: log_sequence_start(c))
                
                # --- Measurement 1 (MAX = 0x1F) ---
                # --- Start of sequence from TRIM_TC.txt ---
                i2c_write(0x19, 0x02) # Software Reset
                time.sleep(10 / 1000.0)
                i2c_write(0x55, 0xA1) # TEST_KEY
                i2c_write(0x56, 0xA2) # NVM_KEY
                i2c_write(0x02, 0x00) # OP Mode Off, Temp Off, Seq2 Off
                i2c_write(0x3A, 0x31) # I2C_SPR_EN On, EOC_SEL On
                i2c_write(0x06, 0x7F) # Pulse Width 56us
                i2c_write(0x1D, 0xFF) # LED1 Current Sel 61.8mA
                i2c_write(0x65, 0x01) # OSC testmode
                i2c_write(0x66, 0x01) # OSC MCLK_EN
                i2c_write(0x5C, 0x01) # ALC testmode
                i2c_write(0x5D, 0xBF) # ALC DAC = 11111111111
                i2c_write(0x5E, 0x1F)

                # Set LED1_TRIM, LED2_TRIM
                i2c_write(0x3F, 0x04) # NVM_ADDR 4
                i2c_write(0x40, 0x20) # Set LED1_TRIM[5:0](=10000)
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                i2c_write(0x3F, 0x05) # NVM_ADDR 5
                i2c_write(0x40, 0x60) # Set LED2_TRIM[5:0](=10000) + CLK5M_DIV(=01)
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                # Set TC_TRIM code using the loop variable 'i'
                i2c_write(0x3F, 0x06) # NVM_ADDR 6
                i2c_write(0x40, 64+i) # Set TC_TRIM[4:0] + TC_EN
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                # Set TC_SEL_LED1 = MAX or MIN
                i2c_write(0x3F, 0x07) # NVM_ADDR 7
                #i2c_write(0x40, 0x00) # MIN
                i2c_write(0x40, 0x1F) # MAX + DN
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                i2c_write(0x3C, 0x8B) # INT_MAN, RSTB_MAN, RSTB OEN=1, IE=1
                i2c_write(0x6E, 0x43) # INT=ALC_FIND, RSTB=CLK_IN
                i2c_write(0x01, 0x01) # S_SENSE ON
                #time.sleep(10 / 1000.0)
                if arduino: arduino.write(b"t,3200\n")
                time.sleep(1.5)
                raw_value_1 = _get_fast_current()

                # --- Measurement 2 (MAX = 0x3F) ---
                # --- Start of sequence from TRIM_TC.txt ---
                i2c_write(0x19, 0x02) # Software Reset
                time.sleep(10 / 1000.0)
                i2c_write(0x55, 0xA1) # TEST_KEY
                i2c_write(0x56, 0xA2) # NVM_KEY
                i2c_write(0x02, 0x00) # OP Mode Off, Temp Off, Seq2 Off
                i2c_write(0x3A, 0x31) # I2C_SPR_EN On, EOC_SEL On
                i2c_write(0x06, 0x7F) # Pulse Width 56us
                i2c_write(0x1D, 0xFF) # LED1 Current Sel 61.8mA
                i2c_write(0x65, 0x01) # OSC testmode
                i2c_write(0x66, 0x01) # OSC MCLK_EN
                i2c_write(0x5C, 0x01) # ALC testmode
                i2c_write(0x5D, 0xBF) # ALC DAC = 11111111111
                i2c_write(0x5E, 0x1F)

                # Set LED1_TRIM, LED2_TRIM
                i2c_write(0x3F, 0x04) # NVM_ADDR 4
                i2c_write(0x40, 0x20) # Set LED1_TRIM[5:0](=10000)
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                i2c_write(0x3F, 0x05) # NVM_ADDR 5
                i2c_write(0x40, 0x60) # Set LED2_TRIM[5:0](=10000) + CLK5M_DIV(=01)
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                # Set TC_TRIM code using the loop variable 'i'
                i2c_write(0x3F, 0x06) # NVM_ADDR 6
                i2c_write(0x40, 64+i) # Set TC_TRIM[4:0] + TC_EN
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                # Set TC_SEL_LED1 = MAX or MIN + TC_UP
                i2c_write(0x3F, 0x07) # NVM_ADDR 7
                #i2c_write(0x40, 0x00) # MIN
                i2c_write(0x40, 0x3F) # MAX + UP
                i2c_write(0x41, 0x10) # NVM_UPDATE
                i2c_write(0x41, 0x00) # NVM_UPDATE DONE

                i2c_write(0x3C, 0x8B) # INT_MAN, RSTB_MAN, RSTB OEN=1, IE=1
                i2c_write(0x6E, 0x43) # INT=ALC_FIND, RSTB=CLK_IN
                i2c_write(0x01, 0x01) # S_SENSE ON
                #time.sleep(10 / 1000.0)
                if arduino: arduino.write(b"t,3200\n")
                time.sleep(1.5)
                raw_value_2 = _get_fast_current()

                log_line = ""
                numeric_value_1, numeric_value_2 = None, None
                
                try:
                    if raw_value_1:
                        numeric_value_1 = float(raw_value_1.split(',')[-1])
                    if raw_value_2:
                        numeric_value_2 = float(raw_value_2.split(',')[-1])

                    if numeric_value_1 is not None and numeric_value_2 is not None:
                        diff_current = abs(numeric_value_1 - numeric_value_2)
                        results.append({'code': i, 'diff': diff_current, 'val1': numeric_value_1, 'val2': numeric_value_2})
                        log_line = (f"  TC CODE {i}:\n"
                                    f"    -> M1 (0x1F): {numeric_value_1 * 1000:.3f} mA\n"
                                    f"    -> M2 (0x3F): {numeric_value_2 * 1000:.3f} mA\n"
                                    f"    -> Diff: {diff_current * 1000:.3f} mA\n")
                    else:
                        log_line = f"  TC CODE {i}: DMM read failed on one or both measurements.\n"
                
                except (ValueError, IndexError):

                    log_line = f"  TC CODE {i}: Parse Error on DMM value.\n"
                
                def update_gui_and_file(line_to_log):
                    log_text.insert(tk.END, line_to_log)
                    log_text.see(tk.END)
                    f.write(line_to_log)
                root.after(0, lambda line=log_line: update_gui_and_file(line))

            # Analysis of results
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
                    # For the final result, we care about the average current of the best code
                    avg_of_best_pair = (best_result_entry['val1'] + best_result_entry['val2']) / 2.0
                    best_current = avg_of_best_pair
                    analysis_result = (
                        f"\n[ANALYSIS] Best TC code (min difference) found: {best_code} (0x{best_code:02X})\n"
                        f"[ANALYSIS] -> with Difference: {min_diff_value * 1000:.3f} mA\n"
                        f"[ANALYSIS] -> The average current for this code is: {best_current * 1000:.3f} mA\n"
                    )
                else:
                    analysis_result = "[ANALYSIS] Could not determine a best code.\n"


            f.write(analysis_result)

        def final_message(result_text, final_current, final_code):
            log_text.insert(tk.END, f"\n[INFO] TC Search (Method 2) complete. Results appended to {filename}\n")
            log_text.insert(tk.END, result_text)
            log_text.see(tk.END)
            
            # This needs to match the button variable name in your GUI creation part
            # Assuming it is tc_search_m2_button
            tc_search_m2_button.config(state=tk.NORMAL) 

            if final_code != -1:
                tc_result_var.set(f"{final_current * 1000:.3f} mA")
                tc_trim_code_var.set(f"{final_code} / 0x{final_code:02X}")
        root.after(0, lambda: final_message(analysis_result, best_current, best_code))

    except Exception as e:
        def error_message():
            messagebox.showerror("TC Search (Method 2) Error", str(e))
            # This also needs to match your button variable name
            tc_search_m2_button.config(state=tk.NORMAL)
        root.after(0, error_message)




def run_bgr_search():
    """Starts the BGR search in a new thread."""
    if not arduino or not multimeter:
        messagebox.showerror("Connection Error", "Please connect both Arduino and Multimeter.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an IC Sample Number.")
        return
    
    bgr_search_button.config(state=tk.DISABLED)
    log_text.insert(tk.END, f"[INFO] Starting BGR Search for Sample: {sample_num} (64-96)...\n")
    search_thread = threading.Thread(target=bgr_search_worker, args=(sample_num,), daemon=True)
    search_thread.start()

def run_mclk_search():
    """Starts the MCLK search in a new thread."""
    if not arduino or not multimeter:
        messagebox.showerror("Connection Error", "Please connect both Arduino and Multimeter.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an IC Sample Number.")
        return
    
    mclk_search_button.config(state=tk.DISABLED)
    log_text.insert(tk.END, f"[INFO] Starting MCLK Search for Sample: {sample_num} (16-64)...\n")
    search_thread = threading.Thread(target=mclk_search_worker, args=(sample_num,), daemon=True)
    search_thread.start()

def run_wclk_search():
    """Starts the WCLK search in a new thread."""
    if not arduino or not multimeter:
        messagebox.showerror("Connection Error", "Please connect both Arduino and Multimeter.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an IC Sample Number.")
        return
    
    wclk_search_button.config(state=tk.DISABLED)
    log_text.insert(tk.END, f"[INFO] Starting WCLK Search for Sample: {sample_num} (0-31)...\n")
    search_thread = threading.Thread(target=wclk_search_worker, args=(sample_num,), daemon=True)
    search_thread.start()

def run_led1_search():
    """Starts the LED1 search in a new thread."""
    if not arduino or not multimeter:
        messagebox.showerror("Connection Error", "Please connect both Arduino and Multimeter.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an IC Sample Number.")
        return
    
    led1_search_button.config(state=tk.DISABLED)
    log_text.insert(tk.END, f"[INFO] Starting LED1 Search for Sample: {sample_num} (16-48)...\n")
    search_thread = threading.Thread(target=led1_search_worker, args=(sample_num,), daemon=True)
    search_thread.start()

def run_led2_search():
    """Starts the LED2 search in a new thread."""
    if not arduino or not multimeter:
        messagebox.showerror("Connection Error", "Please connect both Arduino and Multimeter.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an IC Sample Number.")
        return
    
    led2_search_button.config(state=tk.DISABLED)
    log_text.insert(tk.END, f"[INFO] Starting LED2 Search for Sample: {sample_num} (16-48)...\n")
    search_thread = threading.Thread(target=led2_search_worker, args=(sample_num,), daemon=True)
    search_thread.start()

def run_tc_search():
    """Starts the TC search in a new thread."""
    if not arduino or not multimeter:
        messagebox.showerror("Connection Error", "Please connect both Arduino and Multimeter.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an IC Sample Number.")
        return

    # --- New code to get target current ---
    target_current_amps = 0.0
    try:
        # 1. Get the value from the tc_min_result_var (e.g., "61.800 mA")
        tc_min_str = tc_min_result_var.get()
        if not tc_min_str or "mA" not in tc_min_str:
            messagebox.showerror("Input Error", "Please run 'TRIM_TC_MIN' and 'Measure Current' first to get a target value (in mA).")
            return
        
        # 2. Parse the string
        value_str, unit = tc_min_str.split()
        target_current_ma = float(value_str)
        
        # 3. Convert from mA to A
        target_current_amps = target_current_ma / 1000.0

    except (ValueError, TypeError, IndexError):
        messagebox.showerror("Parse Error", f"Could not parse the TC MIN target value: '{tc_min_str}'.\nPlease run 'TRIM_TC_MIN' and 'Measure Current' again.")
        return
    # --- End of new code ---
    
    tc_search_button.config(state=tk.DISABLED)
    log_text.insert(tk.END, f"[INFO] Starting TC Search for Sample: {sample_num} (0-31)...\n")
    # Log the target current being used
    log_text.insert(tk.END, f"[INFO] Using TC Target Current: {target_current_amps * 1000:.3f} mA\n")
    
    # Pass the new target_current_amps to the worker
    search_thread = threading.Thread(target=tc_search_worker, args=(sample_num, target_current_amps,), daemon=True)
    search_thread.start()

def run_tc_search_m2():
    """Starts the TC search in a new thread."""
    if not arduino or not multimeter:
        messagebox.showerror("Connection Error", "Please connect both Arduino and Multimeter.")
        return
    sample_num = entry_sample_num.get().strip()
    if not sample_num:
        messagebox.showerror("Input Error", "Please enter an IC Sample Number.")
        return

    # --- New code to get target current ---
    target_current_amps = 0.0
    try:
        # 1. Get the value from the tc_min_result_var (e.g., "61.800 mA")
        tc_min_str = tc_min_result_var.get()
        if not tc_min_str or "mA" not in tc_min_str:
            messagebox.showerror("Input Error", "Please run 'TRIM_TC_MIN' and 'Measure Current' first to get a target value (in mA).")
            return
        
        # 2. Parse the string
        value_str, unit = tc_min_str.split()
        target_current_ma = float(value_str)
        
        # 3. Convert from mA to A
        target_current_amps = target_current_ma / 1000.0

    except (ValueError, TypeError, IndexError):
        messagebox.showerror("Parse Error", f"Could not parse the TC MIN target value: '{tc_min_str}'.\nPlease run 'TRIM_TC_MIN' and 'Measure Current' again.")
        return
    # --- End of new code ---
    
    tc_search_m2_button.config(state=tk.DISABLED)
    log_text.insert(tk.END, f"[INFO] Starting TC Search for Sample: {sample_num} (0-31)...\n")
    # Log the target current being used
    log_text.insert(tk.END, f"[INFO] Using TC Target Current: {target_current_amps * 1000:.3f} mA\n")
    
    # Pass the new target_current_amps to the worker
    search_thread = threading.Thread(target=tc_search_worker_m2, args=(sample_num, target_current_amps,), daemon=True)
    search_thread.start()

def run_nvm_program():
    global best_bgr_code, best_mclk_code, best_wclk_code

    if not power_supply:
        messagebox.showerror("Connection Error", "Power Supply not connected.")
        return
    
    if best_bgr_code == -1 or best_mclk_code == -1 or best_wclk_code == -1:
        messagebox.showerror("Prerequisite Error", "Please run BGR, MCLK, and WCLK Search successfully before NVM Programming.")
        return
    
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
        log_text.insert(tk.END, "[NVM] Starting NVM Programming with found codes...\n")
        log_text.insert(tk.END, f"[NVM] Sample Num: {sample_num_int}\n")
        log_text.insert(tk.END, f"[NVM] Using BGR Code: {best_bgr_code}, MCLK Code: {best_mclk_code}, WCLK Code: {best_wclk_code}\n")
        
        # 1. Power Supply ON
        voltage = 7.0
        current = 0.5
        log_text.insert(tk.END, f"[PSU] Setting ON. V={voltage}V, I={current}A\n")
        
        ser = power_supply
        ser.write(b'*CLS\n'); time.sleep(0.05)
        ser.write(b'*RST\n'); time.sleep(0.05)
        ser.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
        ser.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
        ser.write(b'OUTP ON\n'); time.sleep(0.2)
        time.sleep(2.0)

        log_text.insert(tk.END, "[PSU] Output is ON\n")
        root.update_idletasks()

        # 2. Execute NVM Programming Sequence
        i2c_write(0x19, 0x02) # Software Reset
        i2c_write(0x55, 0xA1) # TEST_KEY
        i2c_write(0x56, 0xA2) # NVM_KEY
        i2c_write(0x61, 0x01) # REF testmode
        i2c_write(0x62, 0x03) # REF EN
        i2c_write(0x65, 0x01) # OSC testmode
        i2c_write(0x66, 0x01) # OSC MCLK_EN
        i2c_write(0x41, 0xC0) # NVM_EN
        i2c_write(0x42, 0x20) # NVM_VPP_ON
        i2c_write(0x42, 0x21) # NVM_CE

        # Program PGM_WRITE_OK
        log_text.insert(tk.END, f"[NVM] Programming PGM_WRITE_OK ...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x00) # ADD
        i2c_write(0x40, 0x01) # DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program CLK5M_DIV
        log_text.insert(tk.END, f"[NVM] Programming CLK5M_DIV ...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x05) # ADD
        i2c_write(0x40, 0x40) # DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program BGR
        log_text.insert(tk.END, f"[NVM] Programming BGR with code {best_bgr_code}...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x01) # BGR ADD
        i2c_write(0x40, best_bgr_code) # BGR DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program MCLK
        log_text.insert(tk.END, f"[NVM] Programming MCLK with code {best_mclk_code}...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x02) # MCLK ADD
        i2c_write(0x40, best_mclk_code) # MCLK DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program WCLK
        log_text.insert(tk.END, f"[NVM] Programming WCLK with code {best_wclk_code}...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x03) # WCLK ADD
        i2c_write(0x40, best_wclk_code) # WCLK DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program ADC_REG[3:2] = 11
        log_text.insert(tk.END, f"[NVM] Programming ADC_REG[3:2] ...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x07) # ADD
        i2c_write(0x40, 0xC0) # DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program ADC_REG[1:0] = 11
        log_text.insert(tk.END, f"[NVM] Programming ADC_REG[3:2] ...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x08) # ADD
        i2c_write(0x40, 0xC0) # DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program Sample Month
        log_text.insert(tk.END, f"[NVM] Programming Sample Month {sample_num_m_int} ...\n"); root.update_idletasks()
        i2c_write(0x3F, 10) # ADD
        i2c_write(0x40, sample_num_m_int) # DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program Sample Number
        log_text.insert(tk.END, f"[NVM] Programming Sample Date {sample_num_d_int} ...\n"); root.update_idletasks()
        i2c_write(0x3F, 11) # ADD
        i2c_write(0x40, sample_num_d_int) # DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program Sample Number
        log_text.insert(tk.END, f"[NVM] Programming Sample Num{sample_num_int} ...\n"); root.update_idletasks()
        i2c_write(0x3F, 12) # ADD
        i2c_write(0x40, sample_num_int) # DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # 3. Power Supply OFF
        pwr_off()
        log_text.insert(tk.END, "[NVM] NVM Programming Sequence Finished.\n")

    except Exception as e:
        messagebox.showerror("NVM Program Error", f"An error occurred during the NVM sequence:\n{str(e)}")
        if power_supply:
            try:
                pwr_off()
                log_text.insert(tk.END, "[PSU] Output turned OFF due to error.\n")
            except Exception as psu_e:
                log_text.insert(tk.END, f"[PSU] Failed to turn off PSU during error handling: {psu_e}\n")


def run_nvm_program_led():
    global best_led1_code, best_led2_code, best_tc_code

    if not power_supply:
        messagebox.showerror("Connection Error", "Power Supply not connected.")
        return
    
    if best_led1_code == -1 or best_led2_code == -1 or best_tc_code == -1:
        messagebox.showerror("Prerequisite Error", "Please run TC, LED1, and LED2 Search successfully before NVM Programming.")
        return

    try:
        log_text.insert(tk.END, "[NVM] Starting NVM Programming with found codes...\n")
        log_text.insert(tk.END, f"[NVM] Using TC Code: {best_tc_code}, LED1 Code: {best_led1_code}, LED2 Code: {best_led2_code}\n")
        
        # 1. Power Supply ON
        voltage = 7.0
        current = 0.5
        log_text.insert(tk.END, f"[PSU] Setting ON. V={voltage}V, I={current}A\n")
        
        ser = power_supply
        ser.write(b'*CLS\n'); time.sleep(0.05)
        ser.write(b'*RST\n'); time.sleep(0.05)
        ser.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
        ser.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
        ser.write(b'OUTP ON\n'); time.sleep(0.2)
        time.sleep(2.0)

        log_text.insert(tk.END, "[PSU] Output is ON\n")
        root.update_idletasks()

        # 2. Execute NVM Programming Sequence
        i2c_write(0x19, 0x02) # Software Reset
        i2c_write(0x55, 0xA1) # TEST_KEY
        i2c_write(0x56, 0xA2) # NVM_KEY
        i2c_write(0x61, 0x01) # REF testmode
        i2c_write(0x62, 0x03) # REF EN
        i2c_write(0x65, 0x01) # OSC testmode
        i2c_write(0x66, 0x01) # OSC MCLK_EN
        i2c_write(0x41, 0xC0) # NVM_EN
        i2c_write(0x42, 0x20) # NVM_VPP_ON
        i2c_write(0x42, 0x21) # NVM_CE

        
        # Program TC
        log_text.insert(tk.END, f"[NVM] Programming TC with code {best_tc_code}...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x06) # TC ADD
        i2c_write(0x40, best_tc_code) # TC DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program LED1
        log_text.insert(tk.END, f"[NVM] Programming LED1 with code {best_led1_code}...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x04) # LED1 ADD
        i2c_write(0x40, best_led1_code) # LED1 DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program LED2
        log_text.insert(tk.END, f"[NVM] Programming LED2 with code {best_led2_code}...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x05) # LED2 ADD
        i2c_write(0x40, best_led2_code) # LED2 DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # 3. Power Supply OFF
        pwr_off()
        log_text.insert(tk.END, "[NVM] NVM Programming Sequence Finished.\n")


    except Exception as e:
        messagebox.showerror("NVM Program Error", f"An error occurred during the NVM sequence:\n{str(e)}")
        if power_supply:
            try:
                pwr_off()
                log_text.insert(tk.END, "[PSU] Output turned OFF due to error.\n")
            except Exception as psu_e:
                log_text.insert(tk.END, f"[PSU] Failed to turn off PSU during error handling: {psu_e}\n")



def run_nvm_program_man():
    global best_bgr_code, best_mclk_code, best_wclk_code

    if not power_supply:
        messagebox.showerror("Connection Error", "Power Supply not connected.")
        return
    
    

    try:
        log_text.insert(tk.END, "[NVM] Starting NVM Programming with found codes...\n")
        log_text.insert(tk.END, f"[NVM] Using ADC_REG = 11\n")
        
        # 1. Power Supply ON
        voltage = 7.0
        current = 0.5
        log_text.insert(tk.END, f"[PSU] Setting ON. V={voltage}V, I={current}A\n")
        
        ser = power_supply
        ser.write(b'*CLS\n'); time.sleep(0.05)
        ser.write(b'*RST\n'); time.sleep(0.05)
        ser.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
        ser.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
        ser.write(b'OUTP ON\n'); time.sleep(0.2)
        time.sleep(2.0)

        log_text.insert(tk.END, "[PSU] Output is ON\n")
        root.update_idletasks()

        # 2. Execute NVM Programming Sequence
        i2c_write(0x19, 0x02) # Software Reset
        i2c_write(0x55, 0xA1) # TEST_KEY
        i2c_write(0x56, 0xA2) # NVM_KEY
        i2c_write(0x61, 0x01) # REF testmode
        i2c_write(0x62, 0x03) # REF EN
        i2c_write(0x65, 0x01) # OSC testmode
        i2c_write(0x66, 0x01) # OSC MCLK_EN
        i2c_write(0x41, 0xC0) # NVM_EN
        i2c_write(0x42, 0x20) # NVM_VPP_ON
        i2c_write(0x42, 0x21) # NVM_CE

        

        # Program ADC_REG[3:2] = 11
        log_text.insert(tk.END, f"[NVM] Programming ADC_REG[3:2] ...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x07) # ADD
        i2c_write(0x40, 0xC0) # DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # Program ADC_REG[1:0] = 11
        log_text.insert(tk.END, f"[NVM] Programming ADC_REG[3:2] ...\n"); root.update_idletasks()
        i2c_write(0x3F, 0x08) # ADD
        i2c_write(0x40, 0xC0) # DATA
        i2c_write(0x42, 0x25) # NVM_PGM_ON
        time.sleep(2 / 1000.0) # D,2
        i2c_write(0x42, 0x21) # NVM_PGM_OFF

        # 3. Power Supply OFF
        pwr_off()
        log_text.insert(tk.END, "[NVM] NVM Programming Sequence Finished.\n")

    except Exception as e:
        messagebox.showerror("NVM Program Error", f"An error occurred during the NVM sequence:\n{str(e)}")
        if power_supply:
            try:
                pwr_off()
                log_text.insert(tk.END, "[PSU] Output turned OFF due to error.\n")
            except Exception as psu_e:
                log_text.insert(tk.END, f"[PSU] Failed to turn off PSU during error handling: {psu_e}\n")

# --- Power Supply Control Functions ---
def pwr_on():
    if not power_supply:
        log_text.insert(tk.END, "[ERROR] Power Supply not connected\n")
        return
    try:
        voltage = float(entry_psu_volt.get())
        current = float(entry_psu_amp.get())
        
        log_text.insert(tk.END, f"[PSU] Turning ON. V={voltage}V, I={current}A\n")
        
        ser = power_supply
        ser.write(b'*CLS\n'); time.sleep(0.05)
        ser.write(b'*RST\n'); time.sleep(0.05)
        ser.write(f'VOLT {voltage}\n'.encode()); time.sleep(0.05)
        ser.write(f'CURR {current}\n'.encode()); time.sleep(0.05)
        ser.write(b'OUTP ON\n'); time.sleep(0.1)
        
        log_text.insert(tk.END, f"[PSU] Output is ON\n")
        log_text.see(tk.END)

    except ValueError:
        messagebox.showerror("Input Error", "Voltage and Max Amp must be numbers.")
    except Exception as e:
        messagebox.showerror("Error", str(e))

def pwr_off():
    if not power_supply:
        log_text.insert(tk.END, "[ERROR] Power Supply not connected\n")
        return
    try:
        log_text.insert(tk.END, f"[PSU] Turning OFF\n")
        power_supply.write(b'OUTP OFF\n')
        time.sleep(0.1)
        log_text.insert(tk.END, f"[PSU] Output is OFF\n")
        log_text.see(tk.END)
    except Exception as e:
        messagebox.showerror("Error", str(e))

# ------------------------------
# Console Command Processing
# ------------------------------
def run_console_cmd():
    cmds = console_text_input.get("1.0", tk.END).strip().splitlines()
    for cmd_line in cmds:
        cmd_line = cmd_line.strip()
        if not cmd_line: continue
        log_text.insert(tk.END, f"> {cmd_line}\n")
        log_text.see(tk.END)
        try:
            parts = cmd_line.split("//")[0].strip().split(",")
            cmd = parts[0].upper()
            
            if cmd == "W":
                reg, data = int(parts[1], 16), int(parts[2], 16)
                resp = i2c_write(reg, data)
                log_text.insert(tk.END, f"[I2C WRITE] 0x{reg:02X} <- 0x{data:02X} ({resp})\n")
            elif cmd == "R":
                reg = int(parts[1], 16)
                val = i2c_read(reg)
                log_text.insert(tk.END, f"[I2C READ] 0x{reg:02X} -> 0x{val:02X}\n" if val is not None else f"[I2C READ] 0x{reg:02X} -> Read Failed\n")
            elif cmd == "PULLUP":
                if arduino: arduino.write(b'PULLUP\n'); log_text.insert(tk.END, "[Arduino] Pin7 Pull-up enabled\n")
            elif cmd == "FLOAT":
                if arduino: arduino.write(b'FLOAT\n'); log_text.insert(tk.END, "[Arduino] Pin7 Floating (High-Z)\n")
            elif cmd == "T":
                if len(parts) > 1:
                    count = int(parts[1])
                    if arduino:
                        arduino.write(f"t,{count}\n".encode())
                        log_text.insert(tk.END, f"[PULSE] Sent command to generate {count} pulses via console.\n")
                else:
                    log_text.insert(tk.END, "[ERROR] Pulse count missing for t command.\n")
            elif cmd == "D":
                if len(parts) > 1:
                    delay_ms = int(parts[1])
                    log_text.insert(tk.END, f"[DELAY] Waiting for {delay_ms} ms...\n")
                    root.update_idletasks()
                    time.sleep(delay_ms / 1000.0)
                    log_text.insert(tk.END, f"[DELAY] ...Done.\n")
                else:
                    log_text.insert(tk.END, "[ERROR] Delay time missing for D command.\n")
            else:
                log_text.insert(tk.END, "[ERROR] Unknown command\n")
        except Exception as e:
            log_text.insert(tk.END, f"[ERROR] {str(e)}\n")
    log_text.see(tk.END)

# ------------------------------
# Script Execution
# ------------------------------
def prepare_for_measurement(script_name, filename):
    """Sets the context for the next measurement, runs script, and may trigger auto-measurement."""
    global last_script_context
    last_script_context = script_name
    
    if script_name == "bgr":
        bgr_result_var.set("")
        bgr_trim_code_var.set("")
    elif script_name == "wclk":
        wclk_result_var.set("")
        wclk_trim_code_var.set("")
    elif script_name == "mclk":
        mclk_result_var.set("")
        mclk_trim_code_var.set("")
    elif script_name == "led1":
        led1_result_var.set("")
        led1_trim_code_var.set("")
    elif script_name == "led2":
        led2_result_var.set("")
        led2_trim_code_var.set("")
    elif script_name == "tc":
        tc_result_var.set("")
        tc_trim_code_var.set("")
    
    run_script_from_file(filename)

    if script_name in ["led1", "led2"]:
        root.after(100, measure_current)

def run_script_from_file(filename):
    """Reads a script from a file and executes it."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, filename)
        with open(file_path, 'r') as f:
            script_content = f.read()
        
        console_text_input.delete("1.0", tk.END)
        console_text_input.insert(tk.END, script_content)
        run_console_cmd()

    except FileNotFoundError:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        messagebox.showerror("File Not Found", f"Script file not found: '{filename}'\n\nPlease ensure the file is in the same folder as the script:\n{script_dir}")
    except Exception as e:
        messagebox.showerror("Error", f"An error occurred while reading the file:\n{str(e)}")


# ------------------------------
# GUI Layout
# ------------------------------
root = tk.Tk()
root.title("IC Control Panel")

# --- StringVars for result labels ---
bgr_result_var = StringVar()
wclk_result_var = StringVar()
mclk_result_var = StringVar()
led1_result_var = StringVar()
led2_result_var = StringVar()
tc_min_result_var = StringVar()
tc_result_var = StringVar()

bgr_trim_code_var = StringVar()
led1_trim_code_var = StringVar()
led2_trim_code_var = StringVar()
mclk_trim_code_var = StringVar()
wclk_trim_code_var = StringVar()
tc_trim_code_var = StringVar()

# --- Device Connection ---
frame_com = tk.LabelFrame(root, text="Device Connection", padx=10, pady=5)
frame_com.pack(padx=10, pady=5, fill="x")

# Arrange device controls horizontally: each device in its own column
# Arduino column
tk.Label(frame_com, text="Arduino COM").grid(row=0, column=0, sticky="w", pady=2, padx=(0,5))
com_arduino_var = StringVar(value="COM13")
tk.OptionMenu(frame_com, com_arduino_var, *scan_com_ports()).grid(row=1, column=0, padx=(0,5))

# place Open/Close side-by-side using a small frame
btn_frame_arduino = tk.Frame(frame_com)
btn_frame_arduino.grid(row=2, column=0, padx=(0,5), pady=2)
tk.Button(btn_frame_arduino, text="Open", command=open_arduino).pack(side="left", padx=(0,5))
tk.Button(btn_frame_arduino, text="Close", command=close_arduino).pack(side="left")

# Multimeter column
tk.Label(frame_com, text="Multimeter COM").grid(row=0, column=1, sticky="w", pady=2, padx=(10,5))
com_dmm_var = StringVar(value="COM5")
tk.OptionMenu(frame_com, com_dmm_var, *scan_com_ports()).grid(row=1, column=1, padx=(10,5))
btn_frame_dmm = tk.Frame(frame_com)
btn_frame_dmm.grid(row=2, column=1, padx=(10,5), pady=2)
tk.Button(btn_frame_dmm, text="Open", command=open_multimeter).pack(side="left", padx=(0,5))
tk.Button(btn_frame_dmm, text="Close", command=close_multimeter).pack(side="left")

# Multimeter 2 column
tk.Label(frame_com, text="Multimeter 2 COM").grid(row=0, column=2, sticky="w", pady=2, padx=(10,5))
com_dmm2_var = StringVar(value="COM11")
tk.OptionMenu(frame_com, com_dmm2_var, *scan_com_ports()).grid(row=1, column=2, padx=(10,5))
btn_frame_dmm2 = tk.Frame(frame_com)
btn_frame_dmm2.grid(row=2, column=2, padx=(10,5), pady=2)
tk.Button(btn_frame_dmm2, text="Open", command=open_multimeter2).pack(side="left", padx=(0,5))
tk.Button(btn_frame_dmm2, text="Close", command=close_multimeter2).pack(side="left")

# Power Supply column
tk.Label(frame_com, text="Power Supply COM").grid(row=0, column=3, sticky="w", pady=2, padx=(10,0))
com_psu_var = StringVar(value="COM12")
tk.OptionMenu(frame_com, com_psu_var, *scan_com_ports()).grid(row=1, column=3, padx=(10,0))
btn_frame_psu = tk.Frame(frame_com)
btn_frame_psu.grid(row=2, column=3, padx=(10,0), pady=2)
tk.Button(btn_frame_psu, text="Open", command=open_psu).pack(side="left", padx=(0,5))
tk.Button(btn_frame_psu, text="Close", command=close_psu).pack(side="left")
# --- Sample Info ---
frame_sample = tk.LabelFrame(root, text="Sample Info", padx=10, pady=5)
frame_sample.pack(padx=10, pady=5, fill="x")
tk.Label(frame_sample, text="IC Sample Num:").pack(side="left", padx=5)
entry_sample_num = tk.Entry(frame_sample, width=20)
entry_sample_num.pack(side="left", padx=5)

tk.Label(frame_sample, text="Sample Month:").pack(side="left", padx=5)
entry_sample_num_m = tk.Entry(frame_sample, width=10) # Added new entry
entry_sample_num_m.pack(side="left", padx=5)

tk.Label(frame_sample, text="Sample Date:").pack(side="left", padx=5)
entry_sample_num_d = tk.Entry(frame_sample, width=10) # Added new entry
entry_sample_num_d.pack(side="left", padx=5)


# --- I2C Control (Address, Write, Read) ---
frame_i2c = tk.LabelFrame(root, text="I2C Control", padx=10, pady=5)
frame_i2c.pack(padx=10, pady=5, fill="x")

tk.Label(frame_i2c, text="ADDRESS (Reg, hex)").grid(row=0, column=0, pady=2, padx=5)
entry_i2c_reg = tk.Entry(frame_i2c, width=8)
entry_i2c_reg.grid(row=0, column=1)

tk.Label(frame_i2c, text="DATA (hex)").grid(row=0, column=2, pady=2, padx=5)
entry_i2c_data = tk.Entry(frame_i2c, width=8)
entry_i2c_data.grid(row=0, column=3)

tk.Button(frame_i2c, text="Write", command=write_i2c, width=8).grid(row=0, column=4, padx=(10, 5))
tk.Button(frame_i2c, text="Read", command=read_i2c, width=8).grid(row=0, column=5, padx=5)
tk.Button(frame_i2c, text="NVM_PGM_MAN", command=run_nvm_program_man).grid(row=0, column=12, padx=5)
tk.Button(frame_i2c, text="READ_SAMPLE_NUM", command=run_rd_sample_num).grid(row=0, column=13, padx=5)


# --- Pulse Generator ---
frame_pulse = tk.LabelFrame(root, text="Pulse Generator", padx=10, pady=5)
frame_pulse.pack(padx=10, pady=5, fill="x")

tk.Label(frame_pulse, text="Pulse Count (1-9000)").grid(row=0, column=0, pady=2, padx=5)
entry_pulse_count = tk.Entry(frame_pulse, width=8)
entry_pulse_count.grid(row=0, column=1)
tk.Button(frame_pulse, text="Send Pulses", command=send_pulse_command).grid(row=0, column=2, padx=10)

# --- Instrument Control ---
frame_instruments = tk.Frame(root)
frame_instruments.pack(padx=10, pady=5, fill="x")

frame_dmm = tk.LabelFrame(frame_instruments, text="Multimeter", padx=10, pady=5)
frame_dmm.pack(side="left")
tk.Button(frame_dmm, text="Measure Voltage", command=measure_voltage).pack(side="left", padx=5)
tk.Button(frame_dmm, text="Measure Frequency", command=measure_freq).pack(side="left", padx=5)
tk.Button(frame_dmm, text="Measure Current", command=measure_current).pack(side="left", padx=5)

frame_psu = tk.LabelFrame(frame_instruments, text="PowerSupply", padx=10, pady=5)
frame_psu.pack(side="left", padx=(10, 0))
tk.Label(frame_psu, text="Voltage").grid(row=0, column=0, padx=(0, 2))
entry_psu_volt = tk.Entry(frame_psu, width=8)
entry_psu_volt.grid(row=0, column=1)
tk.Label(frame_psu, text="Max Amp").grid(row=0, column=2, padx=(5, 2))
entry_psu_amp = tk.Entry(frame_psu, width=8)
entry_psu_amp.grid(row=0, column=3)
tk.Button(frame_psu, text="PWR ON", command=pwr_on).grid(row=0, column=4, padx=5)
tk.Button(frame_psu, text="PWR OFF", command=pwr_off).grid(row=0, column=5, padx=5)

# --- Log and Script Buttons Area ---
frame_log_area = tk.Frame(root)
frame_log_area.pack(padx=10, pady=5, fill="both", expand=True)

# Main log on the left
log_text = scrolledtext.ScrolledText(frame_log_area, width=100, height=25, font=("Consolas", 12))
log_text.pack(side="left", fill="both", expand=True)

# Right-side panel that holds scripts and console (horizontally)
right_panel = tk.Frame(frame_log_area)
right_panel.pack(side="right", fill="y", padx=(10, 0))

# --- Test Scripts placed in the right panel (left side) ---
frame_scripts = tk.LabelFrame(right_panel, text="Test Scripts", padx=10, pady=10)
frame_scripts.pack(side="left", fill="y")

tk.Button(frame_scripts, text="TRIM_BGR", command=lambda: prepare_for_measurement("bgr", "TRIM_BGR.txt")).grid(row=0, column=0, sticky="ew", pady=2)
tk.Label(frame_scripts, textvariable=bgr_result_var, relief="sunken", width=12, anchor="w").grid(row=0, column=1, padx=5)
tk.Label(frame_scripts, textvariable=bgr_trim_code_var, relief="sunken", width=12, anchor="w").grid(row=0, column=2, padx=5)

tk.Button(frame_scripts, text="TRIM_MCLK", command=lambda: prepare_for_measurement("mclk", "TRIM_MCLK.txt")).grid(row=1, column=0, sticky="ew", pady=2)
tk.Label(frame_scripts, textvariable=mclk_result_var, relief="sunken", width=12, anchor="w").grid(row=1, column=1, padx=5)
tk.Label(frame_scripts, textvariable=mclk_trim_code_var, relief="sunken", width=12, anchor="w").grid(row=1, column=2, padx=5)

tk.Button(frame_scripts, text="TRIM_WCLK", command=lambda: prepare_for_measurement("wclk", "TRIM_WCLK.txt")).grid(row=2, column=0, sticky="ew", pady=2)
tk.Label(frame_scripts, textvariable=wclk_result_var, relief="sunken", width=12, anchor="w").grid(row=2, column=1, padx=5)
tk.Label(frame_scripts, textvariable=wclk_trim_code_var, relief="sunken", width=12, anchor="w").grid(row=2, column=2, padx=5)

tk.Button(frame_scripts, text="TRIM_TC_MIN", command=lambda: prepare_for_measurement("tc", "TRIM_TC.txt")).grid(row=3, column=0, sticky="ew", pady=2)
tk.Label(frame_scripts, textvariable=tc_min_result_var, relief="sunken", width=12, anchor="w").grid(row=3, column=1, padx=5)
tk.Label(frame_scripts, textvariable=tc_result_var, relief="sunken", width=12, anchor="w").grid(row=4, column=1, padx=5)
tk.Label(frame_scripts, textvariable=tc_trim_code_var, relief="sunken", width=12, anchor="w").grid(row=4, column=2, padx=5)

tk.Button(frame_scripts, text="TRIM_LED1", command=lambda: prepare_for_measurement("led1", "TRIM_LED1.txt")).grid(row=5, column=0, sticky="ew", pady=2)
tk.Label(frame_scripts, textvariable=led1_result_var, relief="sunken", width=12, anchor="w").grid(row=5, column=1, padx=5)
tk.Label(frame_scripts, textvariable=led1_trim_code_var, relief="sunken", width=12, anchor="w").grid(row=5, column=2, padx=5)

tk.Button(frame_scripts, text="TRIM_LED2", command=lambda: prepare_for_measurement("led2", "TRIM_LED2.txt")).grid(row=6, column=0, sticky="ew", pady=2)
tk.Label(frame_scripts, textvariable=led2_result_var, relief="sunken", width=12, anchor="w").grid(row=6, column=1, padx=5)
tk.Label(frame_scripts, textvariable=led2_trim_code_var, relief="sunken", width=12, anchor="w").grid(row=6, column=2, padx=5)



bgr_search_button = tk.Button(frame_scripts, text="BGR Search", command=run_bgr_search)
bgr_search_button.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(10,2))

mclk_search_button = tk.Button(frame_scripts, text="MCLK Search (short JUMP)", command=run_mclk_search)
mclk_search_button.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(2,2))

wclk_search_button = tk.Button(frame_scripts, text="WCLK Search", command=run_wclk_search)
wclk_search_button.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(2,2))

tk.Button(frame_scripts, text="NVM_PGM_BASIC_TRIM", command=run_nvm_program).grid(row=10, column=0, columnspan=3, sticky="ew", pady=(2, 10))

tc_search_button = tk.Button(frame_scripts, text="TC Search", command=run_tc_search)
tc_search_button.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(2,2))

tc_search_m2_button = tk.Button(frame_scripts, text="TC Search method 2", command=run_tc_search_m2)
tc_search_m2_button.grid(row=12, column=0, columnspan=3, sticky="ew", pady=(2,2))

led1_search_button = tk.Button(frame_scripts, text="LED1 Search", command=run_led1_search)
led1_search_button.grid(row=13, column=0, columnspan=3, sticky="ew", pady=(2,2))

led2_search_button = tk.Button(frame_scripts, text="LED2 Search", command=run_led2_search)
led2_search_button.grid(row=14, column=0, columnspan=3, sticky="ew", pady=(2,2))

tk.Button(frame_scripts, text="NVM_PGM_LED_TRIM", command=run_nvm_program_led).grid(row=15, column=0, columnspan=3, sticky="ew", pady=(2, 10))


# --- Console placed to the RIGHT of Test Scripts ---
frame_console = tk.LabelFrame(right_panel, text="Console Input", padx=10, pady=5)
frame_console.pack(side="right", fill="y", padx=(10,0))

frame_console_buttons = tk.Frame(frame_console)
frame_console_buttons.pack(side="left", fill="y", padx=5)
tk.Button(frame_console_buttons, text="Send", command=run_console_cmd).pack(fill="x")
tk.Button(frame_console_buttons, text="Clear Log", command=clear_log).pack(fill="x", pady=5)
tk.Button(frame_console_buttons, text="Clear Input", command=clear_console_input).pack(fill="x")

console_text_input = scrolledtext.ScrolledText(frame_console, width=40, height=20, font=("Consolas", 12))
console_text_input.pack(side="right", padx=5, fill="both", expand=True)

# --- Close Protocol ---
def on_close():
    if arduino: arduino.close()
    if multimeter: multimeter.close()
    if power_supply: power_supply.close()
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()

