classdef TypeSwc_Mode < uint8
    %TYPESWC_MODE Operating mode of the RTE-04 test component (enumeration at a sender-receiver port).
    %   Values with a gap (Fault = 3) so a test notices literals that are numbered by position.
    enumeration
        Off(0)
        Run(1)
        Fault(3)
    end
    methods (Static)
        function value = getDefaultValue()
            value = TypeSwc_Mode.Off;
        end
        function flag = addClassNameToEnumNames()
            % literals TypeSwc_Mode_Off ...: names of several enumerations cannot collide in Rte_Type.h
            flag = true;
        end
        function scope = getDataScope()
            % AUTOSAR: the RTE defines the type and its literals (tools/rtegen, Rte_Type.h)
            scope = 'Imported';
        end
        function file = getHeaderFile()
            file = 'Rte_Type.h';
        end
    end
end
