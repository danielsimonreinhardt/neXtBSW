% SPDX-License-Identifier: Apache-2.0
function swc_map_parameters(mdl, names, port)
%SWC_MAP_PARAMETERS Maps model workspace parameters to one AUTOSAR ParameterReceiver port (RTE-03).
%   swc_map_parameters('DemoSwc', {'FilterAlpha'}) expects the model to be mapped to an AUTOSAR component
%   (autosar.api.create) and a Simulink.Parameter of each name in the model workspace. It
%     - adds the parameter interface <port> (default 'CalPrm') with one data element per name,
%     - adds the ParameterReceiver port <port> of that interface to the component,
%     - declares the parameters as model arguments (ParameterArgumentNames): AUTOSAR Blockset R2024a offers the
%       role 'PortParameter' only for model arguments (plain model workspace parameters: Auto, SharedParameter,
%       ConstantMemory), found out on 2026-09-17,
%     - maps each parameter as PortParameter with calibration access ReadWrite.
%   Embedded Coder then generates Rte_Prm_<port>_<name>() calls; the RTE (tools/rtegen) keeps the values in RAM.
%   The values never come from here: swc_load_data puts those of <Swc>_data.m into the workspace.
%   Used by swc_create (swc.yaml, parameters:) and by the one-time scripts of hand-made models (DemoSwc_autosar).

if nargin < 3 || isempty(port)
    port = 'CalPrm';
end
names = cellstr(names);
if isempty(names)
    return;
end
arProps = autosar.api.getAUTOSARProperties(mdl);
ifPkg = get(arProps, 'XmlOptions', 'InterfacePackage');
iface = [ifPkg '/' port];
if ~any(strcmp(find(arProps, ifPkg, 'ParameterInterface', 'PathType', 'FullyQualified'), iface))
    addPackageableElement(arProps, 'ParameterInterface', ifPkg, port);
end
existing = find(arProps, iface, 'ParameterData', 'PathType', 'FullyQualified');
for k = 1:numel(names)
    if ~any(strcmp(existing, [iface '/' names{k}]))
        add(arProps, iface, 'DataElements', names{k});
    end
end
comp = find(arProps, [], 'AtomicComponent', 'PathType', 'FullyQualified');
comp = comp{1};
if ~any(strcmp(find(arProps, comp, 'ParameterReceiverPort', 'PathType', 'FullyQualified'), [comp '/' port]))
    add(arProps, comp, 'ParameterReceiverPorts', port, 'Interface', port);
end
args = strtrim(strsplit(get_param(mdl, 'ParameterArgumentNames'), ','));
args = args(~cellfun(@isempty, args));
set_param(mdl, 'ParameterArgumentNames', strjoin(unique([args, names], 'stable'), ','));
slMap = autosar.api.getSimulinkMapping(mdl);
for k = 1:numel(names)
    mapParameter(slMap, names{k}, 'PortParameter', 'Port', port, 'DataElement', names{k}, ...
        'SwCalibrationAccess', 'ReadWrite');
end
end
