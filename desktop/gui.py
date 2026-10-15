"""Windows desktop UI. No local HTTP listener or browser extension is required."""
import os
import queue as messaging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .runtime import Queue, Runner, Server, home, load_config, save_config, server_url


class Window:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.queue = Queue()
        self.runner = Runner(self.queue)
        self.messages = messaging.Queue()
        root.title('FBR-Transcript Local')
        root.geometry('1100x760')
        root.minsize(900, 650)
        root.configure(background='#eef2f7')
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background='#eef2f7')
        style.configure('TLabel', background='#eef2f7', font=('Segoe UI', 10))
        style.configure('TButton', font=('Segoe UI', 10), padding=8)
        style.configure('Title.TLabel', font=('Segoe UI', 23, 'bold'), foreground='#14233d')
        style.configure('Treeview', rowheight=32, font=('Segoe UI', 10))
        style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'))
        main = ttk.Frame(root, padding=24)
        main.pack(fill='both', expand=True)
        ttk.Label(main, text='FBR-Transcript Local', style='Title.TLabel').pack(anchor='w')
        ttk.Label(main, text='Links entram aqui. Transcrições chegam ao seu sistema principal.').pack(anchor='w', pady=(3, 16))
        ttk.Label(main, text='Cole um link por linha — YouTube, Instagram, Facebook, TikTok ou X').pack(anchor='w')
        self.links = tk.Text(main, height=4, font=('Segoe UI', 11), relief='solid', bd=1, padx=10, pady=8)
        self.links.pack(fill='x', pady=(7, 10))
        controls = ttk.Frame(main)
        controls.pack(fill='x')
        ttk.Button(controls, text='Adicionar à fila', command=self.add).pack(side='left')
        ttk.Button(controls, text='Iniciar / retomar', command=self.start).pack(side='left', padx=8)
        ttk.Button(controls, text='Pausar coleta local', command=self.runner.pause).pack(side='left')
        ttk.Button(controls, text='Configurações', command=self.settings).pack(side='right')
        self.summary = tk.StringVar()
        ttk.Label(main, textvariable=self.summary).pack(anchor='w', pady=(12, 8))
        table = ttk.Frame(main)
        table.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(table, columns=('platform', 'link', 'status'), show='headings', selectmode='browse')
        for column, title, width in [('platform', 'Plataforma', 100), ('link', 'Publicação', 330), ('status', 'Andamento', 480)]:
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=80)
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<Double-1>', lambda event: self.show_result())
        actions = ttk.Frame(main)
        actions.pack(fill='x', pady=(12, 10))
        ttk.Button(actions, text='Ver texto', command=self.show_result).pack(side='left')
        ttk.Button(actions, text='Abrir arquivos locais', command=self.open_files).pack(side='left', padx=8)
        ttk.Button(actions, text='Retomar selecionada', command=self.retry).pack(side='left')
        self.notice = tk.StringVar(value='Configure o servidor e escolha o navegador que já contém suas sessões. Os cookies permanecem neste computador.')
        ttk.Label(main, textvariable=self.notice, wraplength=1000).pack(anchor='w')
        ttk.Label(main, text='Legendas são priorizadas. A alternativa por áudio pode consumir créditos da API configurada no servidor.', wraplength=1000).pack(anchor='w', pady=(5, 0))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()

    def add(self):
        try:
            links = [line.strip() for line in self.links.get('1.0', 'end').splitlines() if line.strip()]
            if not links: return
            if len(links) > 100:
                raise ValueError('Adicione até 100 links por vez.')
            count = self.queue.add(links)
            self.links.delete('1.0', 'end')
            self.notice.set(f'{count} publicação(ões) adicionada(s). Links já presentes não foram duplicados.')
        except ValueError as error:
            messagebox.showerror('Confira os links', str(error), parent=self.root)

    def start(self):
        if self.runner.thread and self.runner.thread.is_alive():
            self.notice.set('Há uma operação em andamento. Aguarde ou pause a coleta local.')
            return
        if self.config['send'] and (not self.config['username'] or not self.config['password']):
            self.settings()
            self.notice.set('Informe o usuário e a senha do Transcript em Configurações.')
            return
        self.runner.start(self.config)

    def selected(self):
        rows = self.tree.selection()
        return self.queue.get(rows[0]) if rows else None

    def retry(self):
        task = self.selected()
        if not task or task['status'] == 'done': return
        if self.runner.thread and self.runner.thread.is_alive(): return
        self.queue.update(task['id'], status='uncertain' if task.get('remote_id') else 'pending', stage='Aguardando retomada segura')
        self.start()

    def open_files(self):
        task = self.selected()
        directory = self.queue.directory / 'outputs' / task['id'] if task else self.queue.directory / 'outputs'
        directory.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt': os.startfile(directory)

    def show_result(self):
        task = self.selected()
        if not task: return
        file = self.queue.directory / 'outputs' / task['id'] / 'transcricao.txt'
        if not file.is_file():
            messagebox.showinfo('Ainda sem texto', task['stage'], parent=self.root)
            return
        window = tk.Toplevel(self.root)
        window.title('Transcrição — ' + task['platform'])
        window.geometry('850x600')
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill='both', expand=True)
        content = tk.Text(frame, wrap='word', font=('Segoe UI', 11), padx=12, pady=12)
        content.pack(fill='both', expand=True)
        content.insert('1.0', file.read_text(encoding='utf-8'))
        content.configure(state='disabled')
        def copy():
            window.clipboard_clear()
            window.clipboard_append(file.read_text(encoding='utf-8'))
        ttk.Button(frame, text='Copiar texto', command=copy).pack(anchor='e', pady=(10, 0))

    def settings(self):
        if self.runner.thread and self.runner.thread.is_alive():
            messagebox.showinfo('Operação em andamento', 'Pause e aguarde o envio em curso terminar antes de alterar as configurações.', parent=self.root)
            return
        window = tk.Toplevel(self.root)
        window.title('Configurações — FBR-Transcript Local')
        window.geometry('720x650')
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill='both', expand=True)
        variables = {}
        for key, label in [('server', 'Servidor principal (HTTPS)'), ('username', 'Usuário do FBR-Transcript'), ('password', 'Senha do FBR-Transcript'),
                           ('profile', 'Pasta do perfil do navegador (opcional)'), ('cookies_file', 'Arquivo cookies.txt local (opcional; substitui o navegador)')]:
            ttk.Label(frame, text=label).pack(anchor='w')
            variables[key] = tk.StringVar(value=self.config.get(key, ''))
            ttk.Entry(frame, textvariable=variables[key], show='*' if key == 'password' else '').pack(fill='x', pady=(3, 8))
        row = ttk.Frame(frame)
        row.pack(fill='x')
        def choose_file():
            name = filedialog.askopenfilename(parent=window, title='Cookies locais no formato Netscape', filetypes=[('Cookies', '*.txt')])
            if name: variables['cookies_file'].set(name)
        ttk.Button(row, text='Escolher cookies.txt', command=choose_file).pack(side='left')
        def choose_profile():
            name = filedialog.askdirectory(parent=window, title='Perfil do navegador')
            if name: variables['profile'].set(name)
        ttk.Button(row, text='Escolher perfil', command=choose_profile).pack(side='left', padx=8)
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=12)
        for key, label, values in [('browser', 'Sessão do navegador', ['none', 'firefox', 'edge', 'chrome']), ('language', 'Idioma preferido', ['auto', 'pt', 'en', 'es', 'fr', 'de', 'it'])]:
            group = ttk.Frame(row)
            group.pack(side='left', padx=(0, 24))
            ttk.Label(group, text=label).pack(anchor='w')
            variables[key] = tk.StringVar(value=self.config[key])
            ttk.Combobox(group, textvariable=variables[key], values=values, state='readonly', width=22).pack()
        for key, label in [('send', 'Enviar resultados ao sistema principal'), ('allow_audio', 'Usar áudio quando não houver legenda utilizável'), ('remember', 'Guardar senha protegida pelo Windows neste usuário')]:
            variables[key] = tk.BooleanVar(value=self.config[key])
            ttk.Checkbutton(frame, text=label, variable=variables[key]).pack(anchor='w', pady=3)
        ttk.Label(frame, text='Use as sessões do navegador deste Windows. Chrome/Edge podem bloquear a leitura de cookies; o Firefox é uma alternativa. Nenhuma senha das redes sociais é solicitada.', wraplength=670).pack(anchor='w', pady=10)
        def read():
            config = {key: value.get() for key, value in variables.items()}
            config['server'] = server_url(config['server'])
            if config['cookies_file'] and not Path(config['cookies_file']).is_file():
                raise ValueError('O arquivo de cookies não foi encontrado.')
            if config['profile'] and not Path(config['profile']).is_dir():
                raise ValueError('A pasta do perfil não foi encontrada.')
            return config
        def save():
            try:
                config = read()
                save_config(config)
                self.config = config
                window.destroy()
                self.notice.set('Configurações salvas. Adicione links e clique em Iniciar / retomar.')
            except ValueError as error:
                messagebox.showerror('Configuração inválida', str(error), parent=window)
        def test():
            try: config = read()
            except ValueError as error:
                messagebox.showerror('Configuração inválida', str(error), parent=window)
                return
            def call():
                server = None
                try:
                    server = Server(config)
                    result = server.capabilities()
                    self.messages.put('Servidor conectado. ' + ('Áudio habilitado.' if result['audio_ready'] else 'Legendas habilitadas; falta configurar a chave de transcrição no servidor.'))
                except Exception as error:
                    self.messages.put(str(error) if isinstance(error, ValueError) else 'Não foi possível conectar. Confira endereço e rede.')
                finally:
                    if server: server.close()
            threading.Thread(target=call, daemon=True).start()
            self.notice.set('Testando conexão com o servidor…')
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=10)
        ttk.Button(buttons, text='Testar conexão', command=test).pack(side='left')
        ttk.Button(buttons, text='Salvar', command=save).pack(side='right')

    def refresh(self):
        tasks = self.queue.all()
        for task in tasks:
            values = (task['platform'], task['url'], task['stage'])
            if self.tree.exists(task['id']): self.tree.item(task['id'], values=values)
            else: self.tree.insert('', 'end', iid=task['id'], values=values)
        running = bool(self.runner.thread and self.runner.thread.is_alive())
        self.summary.set(f"{len(tasks)} publicações  ·  {sum(t['status']=='done' for t in tasks)} concluídas  ·  {'Em processamento' if running else 'Aguardando início / retomada'}")
        while not self.messages.empty():
            self.notice.set(self.messages.get())
        self.root.after(500, self.refresh)

    def close(self):
        self.runner.pause()
        self.root.destroy()
