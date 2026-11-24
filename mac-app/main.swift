// main.swift – die Mac-App "Video Editor".
//
// Was sie macht:
//   1. Startet den Python-Server (app.py) unsichtbar im Hintergrund
//      (oder nutzt einen, der schon läuft, z. B. über "Start Video Editor.command").
//   2. Zeigt die Oberfläche in einem eigenen Fenster (WKWebView = Safari-Technik).
//   3. Beim Beenden (⌘Q oder Fenster schließen) wird der Server wieder gestoppt.
//
// Gebaut wird sie mit build.sh in diesem Ordner. Den Projektordner (wo app.py liegt)
// trägt build.sh in die Info.plist ein (Schlüssel "VEProjectDir").

import Cocoa
import WebKit

let logURL = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Logs/Video Editor.log")
let background = NSColor(red: 0x0f / 255, green: 0x10 / 255, blue: 0x12 / 255, alpha: 1)

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?          // nur gesetzt, wenn WIR den Server gestartet haben
    var serverURL: URL?

    // ------------------------------------------------------------ Start

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        showLoadingScreen("Starte Video Editor…")

        guard let projectDir = projectDirectory() else { return }
        DispatchQueue.global().async { self.startServer(projectDir: projectDir) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        // Nur einen Server beenden, den diese App selbst gestartet hat
        guard let server, server.isRunning else { return }
        server.terminate()
        let deadline = Date().addingTimeInterval(3)
        while server.isRunning && Date() < deadline { usleep(50_000) }
        if server.isRunning { kill(server.processIdentifier, SIGKILL) }
    }

    // ------------------------------------------------------------ Projektordner finden

    func projectDirectory() -> URL? {
        guard let path = Bundle.main.object(forInfoDictionaryKey: "VEProjectDir") as? String else {
            fatal("In der App ist kein Projektordner eingetragen. Bitte mac-app/build.sh erneut ausführen.")
            return nil
        }
        let dir = URL(fileURLWithPath: path)
        let fm = FileManager.default
        if !fm.fileExists(atPath: dir.appendingPathComponent("app.py").path) {
            fatal("Der Projektordner wurde nicht gefunden:\n\(path)\n\nWurde der Ordner verschoben? "
                  + "Dann bitte mac-app/build.sh im neuen Ordner ausführen.")
            return nil
        }
        if !fm.isExecutableFile(atPath: dir.appendingPathComponent(".venv/bin/python").path) {
            // Noch nie eingerichtet -> einmal den Starter im Terminal laufen lassen
            DispatchQueue.main.async {
                let alert = NSAlert()
                alert.messageText = "Einmalige Einrichtung nötig"
                alert.informativeText = "Der Editor wurde noch nicht eingerichtet. Ich öffne dafür einmal "
                    + "„Start Video Editor.command“ im Terminal. Danach kannst du immer die App benutzen."
                alert.addButton(withTitle: "Einrichten")
                alert.addButton(withTitle: "Abbrechen")
                if alert.runModal() == .alertFirstButtonReturn {
                    NSWorkspace.shared.open(dir.appendingPathComponent("Start Video Editor.command"))
                }
                NSApp.terminate(nil)
            }
            return nil
        }
        return dir
    }

    // ------------------------------------------------------------ Server

    /// Läuft der Editor schon? (Antwortet ein Server auf /ping mit unserer Kennung?)
    func findRunningServer() -> URL? {
        for port in 5050..<5060 {
            guard let url = URL(string: "http://127.0.0.1:\(port)/ping"),
                  let data = try? Data(contentsOf: url),
                  String(decoding: data, as: UTF8.self).contains("ki-video-editor") else { continue }
            return URL(string: "http://127.0.0.1:\(port)/")
        }
        return nil
    }

    func startServer(projectDir: URL) {
        if let running = findRunningServer() {
            DispatchQueue.main.async { self.open(running) }
            return
        }

        // Protokoll-Datei vorbereiten (Ausgaben des Servers landen hier)
        let fm = FileManager.default
        try? fm.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !fm.fileExists(atPath: logURL.path) { fm.createFile(atPath: logURL.path, contents: nil) }
        let log = try? FileHandle(forWritingTo: logURL)
        log?.seekToEndOfFile()
        log?.write("\n===== Start \(Date()) =====\n".data(using: .utf8)!)

        let process = Process()
        process.executableURL = projectDir.appendingPathComponent(".venv/bin/python")
        process.arguments = ["app.py", "--no-browser", "--managed"]
        process.currentDirectoryURL = projectDir
        var env = ProcessInfo.processInfo.environment
        let home = fm.homeDirectoryForCurrentUser.path
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:\(home)/.local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        env["PYTHONUNBUFFERED"] = "1"
        process.environment = env
        if let log { process.standardOutput = log; process.standardError = log }

        do {
            try process.run()
        } catch {
            fatal("Der Editor-Server konnte nicht gestartet werden:\n\(error.localizedDescription)")
            return
        }
        server = process

        // Warten, bis der Server antwortet (max. 60 s)
        let deadline = Date().addingTimeInterval(60)
        while Date() < deadline {
            if let url = findRunningServer() {
                DispatchQueue.main.async { self.open(url) }
                return
            }
            if !process.isRunning {
                // Beendet sich sofort, wenn schon ein anderer Editor läuft – dann den nehmen
                if let url = findRunningServer() { DispatchQueue.main.async { self.open(url) }; return }
                fatal("Der Editor-Server ist beim Start abgestürzt.\n\nDetails stehen in:\n~/Library/Logs/Video Editor.log")
                return
            }
            usleep(250_000)
        }
        fatal("Der Editor-Server antwortet nicht.\n\nDetails stehen in:\n~/Library/Logs/Video Editor.log")
    }

    func open(_ url: URL) {
        serverURL = url
        webView.load(URLRequest(url: url))
    }

    // ------------------------------------------------------------ Fenster & Menü

    func buildWindow() {
        let config = WKWebViewConfiguration()
        config.mediaTypesRequiringUserActionForPlayback = []
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")  // Rechtsklick › Element-Informationen

        webView = WKWebView(frame: .zero, configuration: config)
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")   // kein weißes Aufblitzen beim Laden

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "Video Editor"
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = background
        window.minSize = NSSize(width: 720, height: 520)
        window.contentView = webView
        window.setFrameAutosaveName("VideoEditorMainWindow")   // Größe/Position merken
        if !window.setFrameUsingName("VideoEditorMainWindow") { window.center() }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Über Video Editor", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Protokoll anzeigen", action: #selector(showLog), keyEquivalent: "")
        appMenu.addItem(withTitle: "Exporte im Finder zeigen", action: #selector(showExports), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Video Editor ausblenden", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Video Editor beenden", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        // Bearbeiten: Kopieren/Einfügen in der Textzeile. Bewusst OHNE "Widerrufen",
        // damit ⌘Z bei der Seite ankommt (= Video-Version zurück).
        let editItem = NSMenuItem(); main.addItem(editItem)
        let editMenu = NSMenu(title: "Bearbeiten")
        editMenu.addItem(withTitle: "Ausschneiden", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Kopieren", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Einsetzen", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Alles auswählen", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu

        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let viewMenu = NSMenu(title: "Darstellung")
        viewMenu.addItem(withTitle: "Neu laden", action: #selector(reload), keyEquivalent: "r")
        viewMenu.addItem(withTitle: "Vollbild", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        viewMenu.items.last?.keyEquivalentModifierMask = [.command, .control]
        viewItem.submenu = viewMenu

        let windowItem = NSMenuItem(); main.addItem(windowItem)
        let windowMenu = NSMenu(title: "Fenster")
        windowMenu.addItem(withTitle: "Im Dock ablegen", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Schließen", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowItem.submenu = windowMenu
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = main
    }

    @objc func reload() {
        if let serverURL { webView.load(URLRequest(url: serverURL)) }
    }

    @objc func showLog() { NSWorkspace.shared.open(logURL) }

    @objc func showExports() {
        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Movies/Video Editor Exports")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        NSWorkspace.shared.open(dir)
    }

    func showLoadingScreen(_ text: String) {
        let html = """
        <html><body style="margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;
        justify-content:center;gap:18px;background:#0f1012;color:#8b8d98;font:15px -apple-system">
        <div style="width:28px;height:28px;border-radius:50%;border:3px solid rgba(255,255,255,.12);
        border-top-color:#7c6cff;animation:s .8s linear infinite"></div><div>\(text)</div>
        <style>@keyframes s{to{transform:rotate(360deg)}}</style></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    func fatal(_ message: String) {
        DispatchQueue.main.async {
            let alert = NSAlert()
            alert.alertStyle = .critical
            alert.messageText = "Video Editor kann nicht starten"
            alert.informativeText = message
            alert.addButton(withTitle: "Beenden")
            alert.addButton(withTitle: "Protokoll anzeigen")
            if alert.runModal() == .alertSecondButtonReturn { self.showLog() }
            NSApp.terminate(nil)
        }
    }

    // ------------------------------------------------------------ Was die Webseite braucht

    // <input type="file"> -> normaler Mac-Öffnen-Dialog
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.movie, .video, .mpeg4Movie, .quickTimeMovie, .avi]
        panel.allowsOtherFileTypes = true
        panel.beginSheetModal(for: window) { response in
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }

    // alert("…")
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    // confirm("…")
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Abbrechen")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    // Links nach außen (falls es mal welche gibt) im normalen Browser öffnen
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, let host = url.host, host != "127.0.0.1",
           action.navigationType == .linkActivated {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        // Eine reingezogene Datei, die die Seite nicht abfängt, soll nicht das Fenster "ersetzen"
        if action.request.url?.isFileURL == true {
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    // Seite abgestürzt (z. B. zu wenig Speicher) -> neu laden statt weißem Fenster
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { reload() }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
