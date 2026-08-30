// 모노레포용 Metro 설정.
// 기본 설정에 "루트 감시"와 "루트 node_modules 탐색"을 더해서,
// packages/shared 같은 워크스페이스 패키지와 루트로 hoist된 의존성을 찾게 해줍니다.
const { getDefaultConfig } = require('expo/metro-config');
const path = require('path');

const projectRoot = __dirname;
const monorepoRoot = path.resolve(projectRoot, '..');

const config = getDefaultConfig(projectRoot);

// 1) 모노레포 전체를 감시 → shared 코드를 고치면 앱에 바로 반영
config.watchFolders = [monorepoRoot];

// 2) 모듈을 "앱 로컬 → 루트 node_modules" 순으로 탐색
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, 'node_modules'),
  path.resolve(monorepoRoot, 'node_modules'),
];

// 3) .svg 를 이미지가 아니라 React 컴포넌트로 변환 (react-native-svg-transformer)
//    추구미 선호도 화면의 항목 아이콘(넥라인·소매·핏 등)이 이 방식으로 로드된다.
config.transformer.babelTransformerPath = require.resolve('react-native-svg-transformer');
config.resolver.assetExts = config.resolver.assetExts.filter((ext) => ext !== 'svg');
config.resolver.sourceExts = [...config.resolver.sourceExts, 'svg'];

module.exports = config;
