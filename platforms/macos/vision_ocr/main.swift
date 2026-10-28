import Foundation
import Vision
import AppKit

struct Block: Codable {
    let text: String
    let bbox: [Double]
    let confidence: Double
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(2)
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("usage: vision_ocr image [orientation]") }
let path = args[1]
let orientationValue = args.count >= 3 ? Int(args[2]) ?? 1 : 1
guard let image = NSImage(contentsOfFile: path),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fail("cannot decode image")
}
let orientation: CGImagePropertyOrientation
switch orientationValue {
case 3: orientation = .down
case 6: orientation = .right
case 8: orientation = .left
default: orientation = .up
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = true
let handler = VNImageRequestHandler(cgImage: cgImage, orientation: orientation, options: [:])
do {
    try handler.perform([request])
} catch {
    fail("vision failed: \(error)")
}
var blocks: [Block] = []
for observation in request.results ?? [] {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let box = observation.boundingBox
    let bbox = [Double(box.minX), Double(1.0 - box.maxY), Double(box.width), Double(box.height)]
    blocks.append(Block(text: candidate.string, bbox: bbox, confidence: Double(candidate.confidence)))
}
let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
let data = try encoder.encode(blocks)
FileHandle.standardOutput.write(data)
