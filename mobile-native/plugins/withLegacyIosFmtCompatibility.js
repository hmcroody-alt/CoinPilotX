const { withPodfile } = require("@expo/config-plugins");

const MARKER = "PulseSoc legacy renderer fmt compatibility";

module.exports = function withLegacyIosFmtCompatibility(config) {
  return withPodfile(config, (podfileConfig) => {
    if (podfileConfig.modResults.contents.includes(MARKER)) return podfileConfig;

    const postInstallEnd = "    )\n  end\nend";
    if (!podfileConfig.modResults.contents.includes(postInstallEnd)) {
      throw new Error("PulseSoc could not locate the React Native post_install block in the generated Podfile.");
    }

    const compatibility = `    )

    # ${MARKER}: Xcode 26 rejects fmt 11's C++20 consteval checks.
    # React Native's standalone fmt pod is compatible with C++17.
    installer.pods_project.targets.each do |target|
      next unless target.name == 'fmt'

      target.build_configurations.each do |build_config|
        build_config.build_settings['CLANG_CXX_LANGUAGE_STANDARD'] = 'c++17'
      end
    end
  end
end`;

    podfileConfig.modResults.contents = podfileConfig.modResults.contents.replace(postInstallEnd, compatibility);
    return podfileConfig;
  });
};
