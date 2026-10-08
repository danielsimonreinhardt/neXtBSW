% SPDX-License-Identifier: Apache-2.0
function blkStruct = slblocks
%SLBLOCKS Shows the neXtBSW component library (NxSwcLib.slx, RTE-07) in the Simulink Library Browser.
%   Active once swc/template is on the MATLAB path (swc_create, swc_build and swc_mil_test add it).
Browser.Library = 'NxSwcLib';
Browser.Name = 'neXtBSW Components';
blkStruct.Browser = Browser;
end
