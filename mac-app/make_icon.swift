// make_icon.swift – zeichnet das App-Icon (1024×1024 PNG).
// Aufruf (macht build.sh automatisch):  swift make_icon.swift ausgabe.png

import Cocoa

let size: CGFloat = 1024
let output = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"

let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size),
                           bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                           colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

// macOS-Icon-Raster: Inhalt 824×824 mit abgerundeten Ecken, mittig
let inset: CGFloat = 100
let body = NSRect(x: inset, y: inset, width: size - 2 * inset, height: size - 2 * inset)
let shape = NSBezierPath(roundedRect: body, xRadius: 185, yRadius: 185)

// leichter Schatten
NSGraphicsContext.current?.saveGraphicsState()
let shadow = NSShadow()
shadow.shadowColor = NSColor.black.withAlphaComponent(0.35)
shadow.shadowBlurRadius = 24
shadow.shadowOffset = NSSize(width: 0, height: -10)
shadow.set()
NSColor.black.setFill()
shape.fill()
NSGraphicsContext.current?.restoreGraphicsState()

// Verlauf: Violett (wie die Akzentfarbe der App) -> dunkles Indigo
let gradient = NSGradient(colors: [
    NSColor(red: 0.62, green: 0.55, blue: 1.00, alpha: 1),
    NSColor(red: 0.42, green: 0.32, blue: 0.95, alpha: 1),
    NSColor(red: 0.20, green: 0.14, blue: 0.48, alpha: 1),
])!
gradient.draw(in: shape, angle: -70)

// Symbole: Filmstreifen + Zauberstab-Funkeln (= KI)
func drawSymbol(_ name: String, in rect: NSRect, weight: NSFont.Weight) {
    let config = NSImage.SymbolConfiguration(pointSize: rect.height, weight: weight)
        .applying(NSImage.SymbolConfiguration(paletteColors: [.white]))
    guard let image = NSImage(systemSymbolName: name, accessibilityDescription: nil)?
        .withSymbolConfiguration(config) else { return }
    // Seitenverhältnis des Symbols beibehalten, mittig in rect
    let ratio = image.size.width / image.size.height
    var target = rect
    if ratio > rect.width / rect.height {
        target.size.height = rect.width / ratio
        target.origin.y += (rect.height - target.height) / 2
    } else {
        target.size.width = rect.height * ratio
        target.origin.x += (rect.width - target.width) / 2
    }
    image.draw(in: target)
}

drawSymbol("film", in: NSRect(x: 250, y: 270, width: 470, height: 430), weight: .medium)
drawSymbol("sparkles", in: NSRect(x: 600, y: 600, width: 230, height: 230), weight: .bold)

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))
print("Icon gespeichert: \(output)")
