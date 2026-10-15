import json
import multiprocessing
import subprocess
import sys
import tkinter as tk

from .extract import binary
from .runtime import home, protect_password, smoke_child


def main():
    multiprocessing.freeze_support()
    if '--self-test' in sys.argv:
        root = tk.Tk()
        root.withdraw()
        from .gui import Window
        window = Window(root)
        root.update_idletasks()
        report = {'interface': root.title() == 'FBR-Transcript Local'}
        for name in ('ffmpeg', 'ffprobe', 'node'):
            path = binary(name)
            report[name] = bool(path) and subprocess.run([path, '--version' if name == 'node' else '-version'], capture_output=True).returncode == 0
        report['windows_credentials'] = protect_password(protect_password('local-self-test'), decrypt=True) == 'local-self-test'
        receiver, sender = multiprocessing.get_context('spawn').Pipe(duplex=False)
        child = multiprocessing.get_context('spawn').Process(target=smoke_child, args=(sender,))
        child.start()
        sender.close()
        report['worker_process'] = receiver.poll(30) and receiver.recv()
        child.join(timeout=5)
        if child.is_alive(): child.terminate()
        receiver.close()
        root.destroy()
        (home() / 'self-test.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return 0 if all(report.values()) else 1
    from .gui import Window
    root = tk.Tk()
    Window(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
