require 'json'

package = JSON.parse(File.read(File.join(__dir__, '..', 'package.json')))

Pod::Spec.new do |s|
  s.name           = 'PulseAppleTranslation'
  s.version        = package['version']
  s.summary        = package['description']
  s.description    = package['description']
  s.license        = package['license']
  s.author         = package['author']
  s.homepage       = package['homepage']
  # Deliberately the project's existing floor, NOT 18.0. Raising it here would
  # drop every iOS 15-17 user off the app entirely just to reach a feature that
  # is supposed to degrade to cloud fallback. All Translation.framework usage is
  # gated with `@available(iOS 18.0, *)` / `if #available` instead.
  s.platforms      = {
    :ios => '15.1'
  }
  s.swift_version  = '5.9'
  s.source         = { git: '' }
  s.static_framework = true

  s.dependency 'ExpoModulesCore'

  # Translation.framework does not exist before iOS 18. It MUST be weak-linked:
  # a hard link makes dyld refuse to launch the app on iOS 15-17, which would
  # turn a gracefully-degrading feature into a launch crash for those users.
  s.weak_frameworks = ['Translation']
  s.frameworks      = ['SwiftUI', 'UIKit']

  s.pod_target_xcconfig = {
    'DEFINES_MODULE' => 'YES',
    'SWIFT_COMPILATION_MODE' => 'wholemodule'
  }

  s.source_files = '**/*.{h,m,swift}'
end
