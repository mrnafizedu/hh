"""\nIX STORY VIEW TOOL BY MANI KHAN\nTELEGRAM @fbtoolzz FOR CPM TOOLS!\nProfessional iX Browser Automation Tool\n"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import threading
import requests
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import queue
import json, pygetwindow as gw, hashlib, platform, subprocess, os, base64, winreg

class IXStoryViewTool:
    def __init__(self, root):
        self.root = root
        self.root.title('IX STORY VIEW TOOL BY MANI KHAN')
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = max(800, int(screen_width * 0.8))
        window_height = max(600, int(screen_height * 0.8))
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2
        self.root.geometry(f'{window_width}x{window_height}+{x}+{y}')
        self.root.configure(bg='#0d1117')
        self.root.resizable(True, True)
        self.root.minsize(800, 600)
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.base_font_size = max(10, min(16, int(screen_width / 100)))
        self.title_font_size = max(18, min(26, int(screen_width / 50)))
        self.button_font_size = max(12, min(16, int(screen_width / 80)))
        self.api_url = tk.StringVar(value='http://127.0.0.1:53200')
        self.num_profiles = tk.IntVar(value=1)
        self.click_count = tk.IntVar(value=49)
        self.click_delay = tk.DoubleVar(value=1.5)
        self.cycle_delay = tk.IntVar(value=0)
        self.window_size = tk.StringVar(value='200x300')
        self.is_running = False
        self.log_queue = queue.Queue()
        self.total_clicks = 0
        
        # Direct UI setup without license check
        self.setup_ui()
        self.check_log_queue()

    def setup_ui(self):
        main_canvas = tk.Canvas(self.root, bg='#0d1117', highlightthickness=0)
        main_scrollbar = tk.Scrollbar(self.root, orient='vertical', command=main_canvas.yview)
        scrollable_frame = tk.Frame(main_canvas, bg='#0d1117')
        scrollable_frame.bind('<Configure>', lambda e: main_canvas.configure(scrollregion=main_canvas.bbox('all')))
        main_canvas.create_window((0, 0), window=scrollable_frame, anchor='nw')
        main_canvas.configure(yscrollcommand=main_scrollbar.set)
        main_canvas.pack(side='left', fill='both', expand=True)
        main_scrollbar.pack(side='right', fill='y')

        def _on_mousewheel(event):
            main_canvas.yview_scroll(int((-1) * (event.delta / 120)), 'units')
        main_canvas.bind_all('<MouseWheel>', _on_mousewheel)
        title_frame = tk.Frame(scrollable_frame, bg='#0d1117')
        title_frame.pack(fill='x', padx=20, pady=10)
        title_label = tk.Label(title_frame, text='IX STORY VIEW TOOL BY MANI KHAN', font=('Arial', self.title_font_size, 'bold'), fg='#ff6b35', bg='#0d1117')
        title_label.pack()
        subtitle_label = tk.Label(title_frame, text='TELEGRAM @fbtoolzz FOR CPM TOOLS!', font=('Arial', self.base_font_size, 'bold'), fg='#00d4ff', bg='#0d1117')
        subtitle_label.pack()
        config_frame = tk.LabelFrame(scrollable_frame, text='⚙️ CONFIGURATION SETTINGS', font=('Arial', 16, 'bold'), fg='#ffffff', bg='#161b22', relief='raised', bd=3)
        config_frame.pack(fill='x', padx=20, pady=10)
        api_frame = tk.Frame(config_frame, bg='#161b22')
        api_frame.pack(fill='x', padx=10, pady=5)
        tk.Label(api_frame, text='🌐 iX Browser API URL:', font=('Arial', 14, 'bold'), fg='#58a6ff', bg='#161b22').pack(anchor='w')
        api_entry = tk.Entry(api_frame, textvariable=self.api_url, font=('Arial', 12), width=50, bg='#21262d', fg='#f0f6fc', insertbackground='#f0f6fc', relief='solid', bd=1)
        api_entry.pack(fill='x', pady=2)
        profiles_frame = tk.Frame(config_frame, bg='#161b22')
        profiles_frame.pack(fill='x', padx=10, pady=5)
        tk.Label(profiles_frame, text='👥 NUMBER OF PROFILES (1-200):', font=('Arial', 14, 'bold'), fg='#7ee787', bg='#161b22').pack(anchor='w')
        profiles_spinbox = tk.Spinbox(profiles_frame, from_=1, to=200, textvariable=self.num_profiles, font=('Arial', 12, 'bold'), width=10, bg='#21262d', fg='#f0f6fc', insertbackground='#f0f6fc', relief='solid', bd=1)
        profiles_spinbox.pack(anchor='w', pady=2)
        click_frame = tk.Frame(config_frame, bg='#161b22')
        click_frame.pack(fill='x', padx=10, pady=5)
        tk.Label(click_frame, text='🖱️ CLICK SETTINGS:', font=('Arial', 14, 'bold'), fg='#ffa657', bg='#161b22').pack(anchor='w')
        click_settings_frame = tk.Frame(click_frame, bg='#161b22')
        click_settings_frame.pack(fill='x', pady=2)
        tk.Label(click_settings_frame, text='Number of Clicks:', font=('Arial', 12, 'bold'), fg='#f0f6fc', bg='#161b22').grid(row=0, column=0, sticky='w', padx=(0, 10))
        click_count_spinbox = tk.Spinbox(click_settings_frame, from_=1, to=1000, textvariable=self.click_count, font=('Arial', 12, 'bold'), width=8, bg='#21262d', fg='#f0f6fc', insertbackground='#f0f6fc', relief='solid', bd=1)
        click_count_spinbox.grid(row=0, column=1, padx=(0, 20))
        tk.Label(click_settings_frame, text='Click Delay (seconds):', font=('Arial', 12, 'bold'), fg='#f0f6fc', bg='#161b22').grid(row=0, column=2, sticky='w', padx=(0, 10))
        click_delay_spinbox = tk.Spinbox(click_settings_frame, from_=0.1, to=10.0, increment=0.1, textvariable=self.click_delay, font=('Arial', 12, 'bold'), width=8, bg='#21262d', fg='#f0f6fc', insertbackground='#f0f6fc', relief='solid', bd=1)
        click_delay_spinbox.grid(row=0, column=3)
        cycle_frame = tk.Frame(config_frame, bg='#161b22')
        cycle_frame.pack(fill='x', padx=10, pady=5)
        tk.Label(cycle_frame, text='⏰ CYCLE DELAY (0-10 minutes):', font=('Arial', 14, 'bold'), fg='#f85149', bg='#161b22').pack(anchor='w')
        cycle_spinbox = tk.Spinbox(cycle_frame, from_=0, to=10, textvariable=self.cycle_delay, font=('Arial', 12, 'bold'), width=10, bg='#21262d', fg='#f0f6fc', insertbackground='#f0f6fc', relief='solid', bd=1)
        cycle_spinbox.pack(anchor='w', pady=2)
        window_frame = tk.Frame(config_frame, bg='#161b22')
        window_frame.pack(fill='x', padx=10, pady=5)
        window_size_row = tk.Frame(window_frame, bg='#161b22')
        window_size_row.pack(fill='x', pady=2)
        tk.Label(window_size_row, text='🖼️ WINDOW SIZE (RECOMMENDED 200x300):', font=('Arial', 14, 'bold'), fg='#ffd700', bg='#161b22').pack(side='left', padx=(0, 10))
        window_sizes = ['100x100', '100x150', '200x200', '200x300', '300x300', '300x400', '400x400', '400x500', '500x500']
        self.window_size_combo = ttk.Combobox(window_size_row, values=window_sizes, textvariable=self.window_size, font=('Arial', 12, 'bold'), state='readonly', width=12)
        self.window_size_combo.pack(side='left', pady=2)
        self.window_size_combo.set('200x300')
        per_line_frame = tk.Frame(window_frame, bg='#161b22')
        per_line_frame.pack(fill='x', pady=5)
        tk.Label(per_line_frame, text='📐 PER LINE MODE:', font=('Arial', 14, 'bold'), fg='#ffd700', bg='#161b22').pack(side='left', padx=(0, 10))
        per_line_options = ['Auto', '5 per line', '10 per line', '15 per line', '20 per line', '25 per line']
        self.per_line_mode = tk.StringVar(value='Auto')
        self.per_line_combo = ttk.Combobox(per_line_frame, values=per_line_options, textvariable=self.per_line_mode, font=('Arial', 12, 'bold'), state='readonly', width=12)
        self.per_line_combo.pack(side='left', pady=2)
        self.per_line_combo.set('Auto')
        auto_arrange_size_frame = tk.Frame(per_line_frame, bg='#161b22')
        auto_arrange_size_frame.pack(side='left', padx=(20, 0), pady=2)
        tk.Label(auto_arrange_size_frame, text='📐 Auto Arrange Size:', font=('Arial', 12, 'bold'), fg='#ffd700', bg='#161b22').pack(side='left', padx=(0, 5))
        auto_arrange_sizes = ['150x200', '200x200', '200x150', '250x200', '200x250', '300x200', '200x300']
        self.auto_arrange_size = tk.StringVar(value='200x200')
        self.auto_arrange_size_combo = ttk.Combobox(auto_arrange_size_frame, values=auto_arrange_sizes, textvariable=self.auto_arrange_size, font=('Arial', 10, 'bold'), state='readonly', width=10)
        self.auto_arrange_size_combo.pack(side='left', padx=(0, 10))
        self.auto_arrange_size_combo.set('200x200')
        auto_arrange_control_frame = tk.Frame(per_line_frame, bg='#161b22')
        auto_arrange_control_frame.pack(side='left', padx=(10, 0), pady=2)
        self.auto_arrange_enabled = tk.BooleanVar(value=False)
        auto_arrange_checkbox = tk.Checkbutton(auto_arrange_control_frame, text='🔄 Auto Arrange', variable=self.auto_arrange_enabled, font=('Arial', 10, 'bold'), fg='#ffd700', bg='#161b22', selectcolor='#007bff', command=self.toggle_auto_arrange)
        auto_arrange_checkbox.pack(side='left', padx=(0, 5))
        auto_arrange_intervals = ['30 seconds', '1 minute', '2 minutes', '3 minutes', '4 minutes', '5 minutes', '6 minutes', '7 minutes', '8 minutes', '9 minutes', '10 minutes']
        self.auto_arrange_interval = tk.StringVar(value='1 minute')
        self.auto_arrange_interval_combo = ttk.Combobox(auto_arrange_control_frame, values=auto_arrange_intervals, textvariable=self.auto_arrange_interval, font=('Arial', 9, 'bold'), state='readonly', width=10)
        self.auto_arrange_interval_combo.pack(side='left', padx=(0, 10))
        self.auto_arrange_interval_combo.set('1 minute')
        auto_arrange_button = tk.Button(per_line_frame, text='🖼️ Auto Arrange Windows', font=('Arial', 12, 'bold'), fg='#ffffff', bg='#007bff', activeforeground='#ffffff', activebackground='#0056b3', relief='raised', bd=2, cursor='hand2', command=self.auto_arrange_windows)
        auto_arrange_button.pack(side='left', padx=(10, 0), pady=2)
        turbo_mode_button = tk.Button(per_line_frame, text='🚀 Turbo Mode', font=('Arial', 12, 'bold'), fg='#ffffff', bg='#28a745', activeforeground='#ffffff', activebackground='#218838', relief='raised', bd=2, cursor='hand2', command=self.start_turbo_mode)
        turbo_mode_button.pack(side='left', padx=(10, 0), pady=2)
        help_button = tk.Button(window_size_row, text='❓ Need Help? Chat With Admin', font=('Arial', 12, 'bold'), fg='#ffffff', bg='#28a745', activeforeground='#ffffff', activebackground='#218838', relief='raised', bd=2, cursor='hand2', command=self.open_telegram_admin)
        help_button.pack(side='right', padx=(10, 0))
        page_frame = tk.Frame(config_frame, bg='#161b22')
        page_frame.pack(fill='x', padx=10, pady=10)
        tk.Label(page_frame, text='📄 PROFILE PAGE SELECTION:', font=('Arial', 14, 'bold'), fg='#ffd700', bg='#161b22').pack(anchor='w')
        page_row = tk.Frame(page_frame, bg='#161b22')
        page_row.pack(fill='x', pady=2)
        tk.Label(page_row, text='Profiles Per Page:', font=('Arial', 12, 'bold'), fg='#f0f6fc', bg='#161b22').pack(side='left', padx=(0, 10))
        self.profiles_per_page = tk.StringVar(value='10')
        profiles_per_page_options = ['5', '10', '20', '30', '50', '100']
        self.profiles_per_page_combo = ttk.Combobox(page_row, values=profiles_per_page_options, textvariable=self.profiles_per_page, font=('Arial', 12, 'bold'), state='readonly', width=8)
        self.profiles_per_page_combo.pack(side='left', padx=(0, 20))
        tk.Label(page_row, text='Start From Page:', font=('Arial', 12, 'bold'), fg='#f0f6fc', bg='#161b22').pack(side='left', padx=(0, 10))
        self.start_page = tk.StringVar(value='1')
        self.start_page_combo = ttk.Combobox(page_row, values=['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15', '16', '17', '18', '19', '20', '25', '30', '40', '50', '100', 'Custom'], textvariable=self.start_page, font=('Arial', 12, 'bold'), state='readonly', width=8)
        self.start_page_combo.pack(side='left', padx=(0, 10))
        tk.Label(page_row, text='Custom Page:', font=('Arial', 12, 'bold'), fg='#f0f6fc', bg='#161b22').pack(side='left', padx=(10, 5))
        self.custom_page_entry = tk.Entry(page_row, font=('Arial', 12, 'bold'), width=8, bg='#21262d', fg='#f0f6fc', insertbackground='#f0f6fc', relief='solid', bd=1)
        self.custom_page_entry.pack(side='left', padx=(0, 10))
        self.profile_range_label = tk.Label(page_row, text='Profile Range: 1-10', font=('Arial', 12, 'bold'), fg='#00ff00', bg='#161b22')
        self.profile_range_label.pack(side='left', padx=(20, 0))
        self.profiles_per_page_combo.bind('<<ComboboxSelected>>', self.update_profile_range)
        self.start_page_combo.bind('<<ComboboxSelected>>', self.update_profile_range)
        self.custom_page_entry.bind('<KeyRelease>', self.update_profile_range)
        urls_frame = tk.LabelFrame(scrollable_frame, text='🔗 FACEBOOK PROFILE URLs', font=('Arial', 16, 'bold'), fg='#ffffff', bg='#161b22', relief='raised', bd=3)
        urls_frame.pack(fill='both', expand=True, padx=20, pady=10)
        tk.Label(urls_frame, text='📝 Enter Facebook Profile URLs (One per line):', font=('Arial', 14, 'bold'), fg='#a5a5a5', bg='#161b22').pack(anchor='w', padx=10, pady=5)
        self.urls_text = scrolledtext.ScrolledText(urls_frame, height=10, font=('Arial', 11), bg='#21262d', fg='#f0f6fc', insertbackground='#f0f6fc', relief='solid', bd=1)
        self.urls_text.pack(fill='both', expand=True, padx=10, pady=5)
        control_frame = tk.Frame(scrollable_frame, bg='#0d1117')
        control_frame.pack(fill='x', padx=20, pady=10)
        self.test_api_button = tk.Button(control_frame, text='🔧 TEST API', font=('Arial', self.button_font_size, 'bold'), bg='#0969da', fg='#ffffff', relief='raised', bd=3, command=self.test_api_connection_gui)
        self.test_api_button.pack(side='left', padx=5)
        self.start_button = tk.Button(control_frame, text='🚀 START AUTOMATION', font=('Arial', self.button_font_size, 'bold'), bg='#1a7f37', fg='#ffffff', relief='raised', bd=3, command=self.start_automation)
        self.start_button.pack(side='left', padx=5)
        self.stop_button = tk.Button(control_frame, text='⏹️ STOP AUTOMATION', font=('Arial', self.button_font_size, 'bold'), bg='#da3633', fg='#ffffff', relief='raised', bd=3, command=self.stop_automation, state='disabled')
        self.stop_button.pack(side='left', padx=5)
        self.clear_button = tk.Button(control_frame, text='🗑️ CLEAR LOGS', font=('Arial', self.button_font_size, 'bold'), bg='#6f42c1', fg='#ffffff', relief='raised', bd=3, command=self.clear_logs)
        self.clear_button.pack(side='left', padx=5)
        stats_frame = tk.LabelFrame(scrollable_frame, text='📊 CLICK STATISTICS', font=('Arial', 16, 'bold'), fg='#ffffff', bg='#161b22', relief='raised', bd=3)
        stats_frame.pack(fill='x', padx=20, pady=10)
        self.total_clicks_label = tk.Label(stats_frame, text='🎯 TOTAL CLICKS: 0', font=('Arial', 24, 'bold'), fg='#ff6b35', bg='#161b22')
        self.total_clicks_label.pack(pady=15)
        log_frame = tk.LabelFrame(scrollable_frame, text='📊 AUTOMATION LOGS', font=('Arial', 16, 'bold'), fg='#ffffff', bg='#161b22', relief='raised', bd=3)
        log_frame.pack(fill='both', expand=True, padx=20, pady=10)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, font=('Consolas', 10), bg='#0d1117', fg='#7ee787', insertbackground='#ffffff', relief='solid', bd=1)
        self.log_text.pack(fill='both', expand=True, padx=10, pady=5)

    def log_message(self, message):
        """Add message to log queue"""
        self.log_queue.put(message)

    def update_total_clicks(self):
        """Update total clicks counter"""
        self.total_clicks += 1
        self.total_clicks_label.config(text=f'🎯 TOTAL CLICKS: {self.total_clicks}')

    def open_telegram_admin(self):
        """Open Telegram admin chat"""
        import webbrowser
        try:
            webbrowser.open('https://t.me/fbtoolzz')
            self.log_message('✅ Opening Telegram Admin Chat...')
        except Exception as e:
            self.log_message(f'❌ Error opening Telegram: {str(e)}')

    def update_profile_range(self, event=None):
        """Update profile range display based on page selection"""
        try:
            profiles_per_page = int(self.profiles_per_page.get())
            if self.start_page.get() == 'Custom':
                custom_page_text = self.custom_page_entry.get().strip()
                if custom_page_text.isdigit():
                    start_page = int(custom_page_text)
                else:
                    start_page = 1
            else:
                start_page = int(self.start_page.get())
            start_profile = (start_page - 1) * profiles_per_page + 1
            end_profile = start_page * profiles_per_page
            self.profile_range_label.config(text=f'Profile Range: {start_profile}-{end_profile}')
            self.num_profiles.set(profiles_per_page)
        except Exception as e:
            self.log_message(f'⚠️ Error updating profile range: {str(e)}')

    def get_profile_range(self):
        """Get the actual profile range to process"""
        try:
            profiles_per_page = int(self.profiles_per_page.get())
            if self.start_page.get() == 'Custom':
                custom_page_text = self.custom_page_entry.get().strip()
                if custom_page_text.isdigit():
                    start_page = int(custom_page_text)
                else:
                    start_page = 1
            else:
                start_page = int(self.start_page.get())
            start_profile = (start_page - 1) * profiles_per_page + 1
            end_profile = start_page * profiles_per_page
            self.log_message('📄 Profile Range:')
            self.log_message(f'   • Page {start_page} with {profiles_per_page} per page')
            self.log_message(f'   • Profile Range: {start_profile}-{end_profile}')
            return (start_profile, end_profile)
        except Exception as e:
            self.log_message(f'⚠️ Error calculating profile range: {str(e)}')
            return (1, 10)

    def check_log_queue(self):
        """Check and display log messages"""
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_text.insert(tk.END, f'{message}\n')
                self.log_text.see(tk.END)
        except queue.Empty:
            pass
        self.root.after(100, self.check_log_queue)

    def clear_logs(self):
        """Clear log text"""
        self.log_text.delete(1.0, tk.END)

    def test_api_connection_gui(self):
        """Test API connection from GUI"""
        self.log_message('🔧 TESTING iX BROWSER API CONNECTION...')
        self.log_message(f'🌐 API URL: {self.api_url.get()}')
        self.test_api_button.config(state='disabled', text='🔄 TESTING...')
        thread = threading.Thread(target=self.test_api_thread)
        thread.daemon = True
        thread.start()

    def test_api_thread(self):
        """Test API in separate thread"""
        try:
            base_url = self.api_url.get()
            self.log_message(f'🔧 Testing iX Browser API Connection...')
            self.log_message(f'🌐 API URL: {base_url}')
            
            # Test your specific endpoints
            endpoints_to_test = [
                '/api/v2/profile-open',
                '/api/v2/profile-opened-list'
            ]
            
            working_endpoints = []
            
            for endpoint in endpoints_to_test:
                try:
                    full_url = f"{base_url}{endpoint}"
                    if endpoint == '/api/v2/profile-open':
                        response = requests.post(full_url, json={'profile_id': 1}, timeout=10)
                    else:
                        response = requests.get(full_url, timeout=5)
                    
                    if response.status_code == 200:
                        working_endpoints.append(endpoint)
                        self.log_message(f'✅ {endpoint} - SUCCESS')
                        result = response.json()
                        self.log_message(f'📋 Response: {result}')
                    else:
                        self.log_message(f'⚠️ {endpoint} - Status: {response.status_code}')
                except requests.exceptions.ConnectionError:
                    self.log_message(f'❌ {endpoint} - Connection Failed')
                except Exception as e:
                    self.log_message(f'⚠️ {endpoint} - Error: {str(e)}')
            
            if working_endpoints:
                self.log_message('🎉 iX BROWSER API IS WORKING!')
                self.log_message(f'✅ Working endpoints: {len(working_endpoints)}/{len(endpoints_to_test)}')
            else:
                self.log_message('❌ No API endpoints are responding')
                self.log_message('💡 Please check:')
                self.log_message('   • iX Browser is running')
                self.log_message('   • API Server is enabled in iX Browser settings')
                self.log_message('   • Correct API URL and port')
                
        except Exception as e:
            self.log_message(f'❌ API test error: {str(e)}')
        finally:
            self.test_api_button.config(state='normal', text='🔧 TEST API')
            self.log_message('============================================================')

    def start_automation(self):
        """Start automation process"""
        if self.is_running:
            return None
        self.is_running = True
        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')
        thread = threading.Thread(target=self.run_automation)
        thread.daemon = True
        thread.start()

    def stop_automation(self):
        """Stop automation process"""
        self.is_running = False
        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')
        self.log_message('🛑 AUTOMATION STOPPED BY USER')

    def run_automation(self):
        """Main automation logic"""
        try:
            urls = self.get_urls_from_input()
            if not urls:
                self.log_message('❌ No valid URLs found! Please enter Facebook profile URLs.')
                self.is_running = False
                self.start_button.config(state='normal')
                self.stop_button.config(state='disabled')
                return
            
            self.log_message('🚀 STARTING IX STORY VIEW AUTOMATION')
            self.log_message('📊 Configuration:')
            self.log_message(f'   • API URL: {self.api_url.get()}')
            self.log_message(f'   • Profiles: {self.num_profiles.get()}')
            self.log_message(f'   • URLs: {len(urls)}')
            self.log_message(f'   • Clicks: {self.click_count.get()}')
            self.log_message(f'   • Click Delay: {self.click_delay.get()}s')
            self.log_message(f'   • Cycle Delay: {self.cycle_delay.get()} minutes')
            self.log_message('============================================================')
            
            # Get profile range
            start_profile, end_profile = self.get_profile_range()
            num_profiles_to_use = min(self.num_profiles.get(), (end_profile - start_profile + 1))
            
            self.log_message(f'🔧 Opening {num_profiles_to_use} profiles...')
            
            # Open profiles and process URLs
            for i in range(num_profiles_to_use):
                if not self.is_running:
                    break
                    
                profile_num = start_profile + i
                self.log_message(f'🚀 Opening Profile {profile_num}')
                
                # Open profile through iX Browser API
                driver = self.open_profile(profile_num)
                if driver:
                    self.log_message(f'✅ Profile {profile_num} opened successfully')
                    
                    # Resize window
                    self.resize_profile_window(profile_num)
                    
                    # Process URLs for this profile
                    for url_index, url in enumerate(urls, 1):
                        if not self.is_running:
                            break
                        self.log_message(f'🌐 Profile {profile_num} - Processing URL {url_index}')
                        self.process_url_with_timing(driver, url, url_index)
                    
                    # Close profile when done
                    self.close_profile(profile_num)
                else:
                    self.log_message(f'❌ Failed to open Profile {profile_num}')
            
            self.is_running = False
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.log_message('🏁 AUTOMATION COMPLETED')
            
        except Exception as e:
            self.log_message(f'❌ ERROR: {str(e)}')
            self.is_running = False
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')

    def open_profile(self, profile_num):
        """Open iX Browser profile"""
        try:
            self.log_message(f'🔧 Opening Profile {profile_num} via API...')
            data = {'profile_id': profile_num}
            
            # Use your specific API endpoint
            response = requests.post(f'{self.api_url.get()}/api/v2/profile-open', json=data, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                error_obj = result.get('error')
                
                # Check if profile started successfully
                if error_obj is None or error_obj == '' or (isinstance(error_obj, dict) and error_obj.get('code') == 0):
                    data_obj = result.get('data', {})
                    debugging_address = data_obj.get('debugging_address')
                    
                    if debugging_address:
                        chrome_options = Options()
                        chrome_options.add_experimental_option('debuggerAddress', debugging_address)
                        
                        try:
                            driver = webdriver.Chrome(options=chrome_options)
                            time.sleep(3)
                            self.log_message(f'🌐 Profile {profile_num} browser connected!')
                            return driver
                        except Exception as e:
                            self.log_message(f'❌ WebDriver error for Profile {profile_num}: {str(e)}')
                    else:
                        self.log_message(f'❌ No debugging address for Profile {profile_num}')
                else:
                    self.log_message(f'❌ Profile {profile_num} API error: {error_obj}')
            else:
                self.log_message(f'❌ Profile {profile_num} API failed: {response.status_code}')
                
        except Exception as e:
            self.log_message(f'❌ Error opening Profile {profile_num}: {str(e)}')
        
        return None

    def close_profile(self, profile_num):
        """Close iX Browser profile using your API endpoint"""
        try:
            self.log_message(f'🔧 Closing Profile {profile_num} via API...')
            data = {'profile_id': profile_num}
            response = requests.post(f'{self.api_url.get()}/api/v2/profile-close', json=data, timeout=5)
            
            if response.status_code == 200:
                self.log_message(f'✅ Profile {profile_num} closed successfully')
            else:
                self.log_message(f'⚠️ Profile {profile_num} close API returned: {response.status_code}')
                
        except Exception as e:
            self.log_message(f'⚠️ Error closing Profile {profile_num}: {str(e)}')

    def process_url_with_timing(self, driver, url, url_index):
        """Process clicking on a specific URL with exact timing - FIXED STORY VIEW LOGIC"""
        try:
            self.log_message(f'🌐 Navigating to URL {url_index}: {url}')
            driver.get(url)
            self.log_message(f'✅ Navigated to URL {url_index}')
            
            self.log_message('⚡ Waiting 5 seconds for page load...')
            time.sleep(5)
            
            # Check if we're on Facebook profile page
            current_url = driver.current_url
            self.log_message(f'🔍 Current URL: {current_url}')
            
            if 'facebook.com' in current_url:
                self.log_message('✅ Successfully on Facebook page')
                
                # IMPROVED STORY VIEW LOGIC
                if self.click_story_elements_improved(driver, url_index):
                    self.log_message(f'🎉 Successfully completed story view for URL {url_index}')
                else:
                    self.log_message(f'❌ Failed to complete story view for URL {url_index}')
            else:
                self.log_message('❌ Not on Facebook page, skipping...')
                
        except Exception as e:
            self.log_message(f'❌ Error processing URL {url_index}: {str(e)}')

    def click_story_elements_improved(self, driver, url_index):
        """IMPROVED STORY VIEW LOGIC - Fixed version"""
        try:
            self.log_message('🖱️ Starting improved story view process...')
            
            # Step 1: Find and click story ring
            self.log_message('🔍 Looking for story ring...')
            story_ring_found = False
            
            # Multiple selectors for story ring
            story_ring_selectors = [
                "circle[class*='x1n2onr6']",
                "circle[class*='x1n2onr6'][r='28']",
                "circle[cx='50'][cy='50']",
                "svg[aria-label*='Story'] circle",
                "div[role='button'] svg circle",
                "a[role='link'] svg circle"
            ]
            
            for selector in story_ring_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        story_ring = elements[0]
                        driver.execute_script("arguments[0].click();", story_ring)
                        self.log_message(f'✅ Story ring clicked with selector: {selector}')
                        story_ring_found = True
                        break
                except:
                    continue
            
            if not story_ring_found:
                self.log_message('❌ Story ring not found')
                return False
            
            self.log_message('⚡ Waiting 3 seconds for story to open...')
            time.sleep(3)
            
            # Step 2: Check if we're in story viewer
            current_url = driver.current_url
            if 'stories' in current_url.lower() or 'story' in current_url.lower():
                self.log_message('✅ Successfully entered story viewer')
                
                # Step 3: Perform multiple clicks/likes on the story
                self.log_message(f'🔄 Performing {self.click_count.get()} clicks on story...')
                clicks_performed = 0
                
                for i in range(self.click_count.get()):
                    if not self.is_running:
                        break
                    
                    # Try multiple like button selectors
                    like_selectors = [
                        "svg[aria-label*='Like']",
                        "svg[aria-label*='like']",
                        "div[aria-label*='Like']",
                        "div[aria-label*='like']",
                        "button[aria-label*='Like']",
                        "button[aria-label*='like']",
                        "div[role='button'] svg",
                        "button svg"
                    ]
                    
                    like_clicked = False
                    for selector in like_selectors:
                        try:
                            like_buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                            if like_buttons:
                                like_button = like_buttons[0]
                                driver.execute_script("arguments[0].click();", like_button)
                                self.log_message(f'✅ Like click {i+1} performed')
                                clicks_performed += 1
                                self.update_total_clicks()
                                like_clicked = True
                                break
                        except:
                            continue
                    
                    if not like_clicked:
                        # If no like button found, try clicking on the story area
                        try:
                            story_area = driver.find_element(By.CSS_SELECTOR, "div[role='main']")
                            driver.execute_script("arguments[0].click();", story_area)
                            self.log_message(f'✅ Story area click {i+1} performed')
                            clicks_performed += 1
                            self.update_total_clicks()
                        except:
                            pass
                    
                    # Wait between clicks
                    time.sleep(self.click_delay.get())
                
                self.log_message(f'🎉 Completed {clicks_performed} clicks on story')
                return True
            else:
                self.log_message('❌ Not in story viewer')
                return False
                
        except Exception as e:
            self.log_message(f'❌ Error in story view process: {str(e)}')
            return False

    def resize_profile_window(self, profile_num):
        """Resize profile window to selected size"""
        try:
            selected_size = self.window_size.get()
            if selected_size:
                width, height = map(int, selected_size.split('x'))
                self.log_message(f'🎯 Profile {profile_num} - Resizing to: {width}x{height}')
                self.resize_window_to_size(profile_num, width, height)
                self.log_message(f'✅ Profile {profile_num} - Resized successfully!')
        except Exception as e:
            self.log_message(f'⚠️ Error resizing Profile {profile_num} window: {str(e)}')

    def resize_window_to_size(self, profile_num, width, height):
        """Window ko specific size mein resize karta hai"""
        try:
            import pygetwindow as gw
            browser_windows = []
            for window in gw.getAllWindows():
                if f'Profile {profile_num}' in window.title or 'iX Browser' in window.title:
                    browser_windows.append(window)
            
            if browser_windows:
                window = browser_windows[0]
                window.resizeTo(width, height)
                self.log_message(f'✅ Profile {profile_num} - Window resized to {width}x{height}')
            return None
        except Exception as e:
            self.log_message(f'❌ Window resize failed for Profile {profile_num}: {str(e)}')

    def get_urls_from_input(self):
        """URLs from text input get karta hai"""
        try:
            all_text = self.urls_text.get(1.0, tk.END).strip()
            all_lines = all_text.split('\n')
            
            # Filter out empty lines and lines that don't look like URLs
            urls = []
            for line in all_lines:
                line = line.strip()
                if line and ('http://' in line or 'https://' in line or 'facebook.com' in line):
                    # Ensure it's a proper URL
                    if not line.startswith(('http://', 'https://')):
                        line = 'https://' + line
                    urls.append(line)
            
            self.log_message(f'📝 Found {len(urls)} valid URLs')
            return urls
        except Exception as e:
            self.log_message(f'❌ URL input error: {str(e)}')
            return []

    def start_turbo_mode(self):
        """Turbo mode - Threaded to prevent freeze"""
        try:
            import threading
            turbo_thread = threading.Thread(target=self._turbo_mode_worker)
            turbo_thread.daemon = True
            turbo_thread.start()
        except Exception as e:
            self.log_message(f'❌ Turbo mode thread error: {str(e)}')

    def _turbo_mode_worker(self):
        """Turbo mode worker thread with batch processing"""
        try:
            self.log_message('🚀 TURBO MODE ACTIVATED!')
            self.is_running = True
            self.start_button.config(state='disabled')
            self.stop_button.config(state='normal')
            
            self.log_message('🔍 Starting turbo mode in background thread...')
            urls = self.get_urls_from_input()
            if not urls:
                self.log_message('❌ No URLs found!')
                self.is_running = False
                self.start_button.config(state='normal')
                self.stop_button.config(state='disabled')
                return
            
            # Use the same automation logic but with faster processing
            self.run_automation()
                
        except Exception as e:
            self.log_message(f'❌ Turbo mode worker error: {str(e)}')
            self.is_running = False
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')

    def auto_arrange_windows(self):
        """Smart resize tiled layout auto arrange windows"""
        try:
            import pygetwindow as gw
            browser_windows = []
            for window in gw.getAllWindows():
                if 'iX Browser' in window.title or 'Chrome' in window.title or 'Facebook' in window.title:
                    browser_windows.append(window)
            
            if not browser_windows:
                self.log_message('❌ No browser windows found!')
                return None
            
            self.log_message(f'🖼️ Auto arranging {len(browser_windows)} windows...')
            
            screen_width = self.screen_width
            screen_height = self.screen_height
            
            if len(browser_windows) == 1:
                # Single window - center it
                window = browser_windows[0]
                window.resizeTo(400, 600)
                window.moveTo(screen_width//2 - 200, screen_height//2 - 300)
                
            else:
                # Multiple windows - arrange in grid
                import math
                cols = math.ceil(math.sqrt(len(browser_windows)))
                rows = math.ceil(len(browser_windows) / cols)
                
                window_width = screen_width // cols
                window_height = screen_height // rows
                
                for i, window in enumerate(browser_windows):
                    row = i // cols
                    col = i % cols
                    x = col * window_width
                    y = row * window_height
                    
                    window.resizeTo(window_width, window_height)
                    window.moveTo(x, y)
            
            self.log_message('✅ Windows arranged successfully!')
            
        except Exception as e:
            self.log_message(f'❌ Smart resize tiled arrangement failed: {str(e)}')

    def toggle_auto_arrange(self):
        """Auto arrange checkbox toggle karta hai"""
        if self.auto_arrange_enabled.get():
            self.log_message('🔄 Auto Arrange enabled!')
        else:
            self.log_message('⏹️ Auto Arrange disabled!')

if __name__ == '__main__':
    root = tk.Tk()
    app = IXStoryViewTool(root)
    root.mainloop()