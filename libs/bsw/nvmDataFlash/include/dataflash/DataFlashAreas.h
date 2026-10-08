// NVM-12: named areas of the data flash and the jobs over them - family neutral, on top of dataflash::IDataFlash.
//
// The project names the areas (project.yaml nvm.data_flash.areas: name, size, purpose, optional offset); tools/nxconfig
// checks them against the partition and lays them out sector aligned, tools/bswgen generates the table
// (nvm/nvmDataFlash.h, NX_DFLASH_AREAS). The purpose the user named on 2026-09-28 is calibration data that can be
// changed in the device (maps, data sets) - an area access, no file store.
//
// An area is read like memory (read(), refused while a flash command runs on the block) and changed by a job:
//  - erase(area):                     every sector of the area, one command per step;
//  - write(area, offset, data, len):  program units in order; each unit is first read - an erased unit is
//                                     programmed and read back, a unit that already holds exactly these bytes is
//                                     skipped (a write repeated after an interruption), any other unit ends the job
//                                     with NOT_ERASED (programming over data is not allowed by the flash module);
// cyclic() advances the job by at most one flash command and never waits for one: BUSY while the job runs, its result
// once in the step that ends it, then OK. Only one job at a time. The data of write() must stay valid until the job
// has ended.
//
// NVM-25: an area may also be read in place (a map evaluated where it lies): holdForDirectRead() gives a
// dataflash::DirectRead scope, areaAddress() the address of a range of the area while that scope holds the block.
//
// NVM-26 (safety review NVM23-S1 b): a job is only accepted while the integration's admission says so (JobAdmission,
// s32k1xx port: not from STANDBY on - the ECU is on its way to a power down). A job accepted before keeps running;
// the run-down cuts it after the command in flight (port: DataFlashRunDown.h).
//
// SYS-44: every flash command is bracketed with the write hooks (begin right before it is launched, end when it has
// ended or was not launched), so the integration can tell the ROM check (safety::romCheckFlashWrites) that a flash
// write is going on. Per command, not per job: a job that waits (the block busy with EEPROM stores) or is never
// advanced keeps no write open, which the ROM check would count against its period.
//
// Not here: consistency of a data set over a power loss (an erase or program interrupted by a reset leaves the
// sector undefined, [RM13] 36.5.8 CAUTION). A data set that must survive that needs two areas and a header with a
// check sum and sequence (calibration data set, TOOL-145/BL-31) - the caller's format, not the area access.

#pragma once

#include "dataflash/IDataFlash.h"

#include <cstdint>

namespace dataflash
{

enum class Purpose : uint8_t
{
    CALIBRATION, // calibration data changed in the device (maps, data sets; project.yaml purpose: calibration)
    DATA         // other data of the application (purpose: data)
};

/** One area as generated (NX_DFLASH_AREAS): offset in the usable data flash and size, both whole sectors. */
struct Area
{
    char const* name;
    uint32_t offset;
    uint32_t size;
    Purpose purpose;
};

/** Called before every flash command and after its end (both or none; SYS-44 flash write indicator). */
struct WriteHooks
{
    void (*begin)();
    void (*end)();
};

/** NVM-26: asked at every erase()/write(); false refuses the new job with NOT_AVAILABLE. nullptr: always accepted. */
using JobAdmission = bool (*)();

class AreaService
{
public:
    static constexpr uint32_t MAX_PROGRAM_SIZE = 32U; // largest program unit a family may report
    static constexpr uint32_t NO_AREA          = 0xFFFFFFFFU;

    constexpr AreaService(
        IDataFlash& flash,
        Area const* const areas,
        uint32_t const count,
        WriteHooks const hooks       = WriteHooks{},
        JobAdmission const admission = nullptr)
    : _flash(flash)
    , _areas(areas)
    , _count(count)
    , _hooks(hooks)
    , _admission(admission)
    , _job(Job::NONE)
    , _launched(false)
    , _verifying(false)
    , _open(false)
    , _result(Status::OK)
    , _position(0U)
    , _end(0U)
    , _data(nullptr)
    , _dataStart(0U)
    , _commands(0U)
    , _refusedJobs(0U)
    , _unit{}
    {}

    AreaService(AreaService const&)            = delete;
    AreaService& operator=(AreaService const&) = delete;

    uint32_t count() const { return _count; }

    Area const* area(uint32_t const index) const { return (index < _count) ? &_areas[index] : nullptr; }

    /** Index of the area named \p name, NO_AREA if there is none. */
    uint32_t find(char const* const name) const
    {
        for (uint32_t i = 0U; i < _count; ++i)
        {
            if (sameText(_areas[i].name, name))
            {
                return i;
            }
        }
        return NO_AREA;
    }

    /**
     * The generated table against the data flash the chip offers: NOT_AVAILABLE without usable data flash, RANGE if
     * an area lies outside it, ALIGNMENT if an area is not whole sectors or the program unit is not supported.
     */
    Status check() const
    {
        Geometry const g = _flash.geometry();
        if ((g.size == 0U) || (g.sectorSize == 0U))
        {
            return Status::NOT_AVAILABLE;
        }
        if ((g.programSize == 0U) || (g.programSize > MAX_PROGRAM_SIZE) || ((g.sectorSize % g.programSize) != 0U))
        {
            return Status::ALIGNMENT;
        }
        for (uint32_t i = 0U; i < _count; ++i)
        {
            Area const& a = _areas[i];
            if ((a.size == 0U) || (a.offset >= g.size) || (a.size > (g.size - a.offset)))
            {
                return Status::RANGE;
            }
            if (((a.offset % g.sectorSize) != 0U) || ((a.size % g.sectorSize) != 0U))
            {
                return Status::ALIGNMENT;
            }
        }
        return Status::OK;
    }

    /** Copies \p length bytes at \p offset of area \p index; BUSY while a flash command runs on the block. */
    Status read(uint32_t const index, uint32_t const offset, uint8_t* const buffer, uint32_t const length)
    {
        Status const s = inArea(index, offset, length);
        if (s != Status::OK)
        {
            return s;
        }
        if (buffer == nullptr)
        {
            return Status::RANGE;
        }
        return _flash.read(_areas[index].offset + offset, buffer, length);
    }

    /**
     * NVM-25: scope of a read in place over the flash of this service (DirectRead): held() while nothing runs on the
     * block; until it ends no command and no EEPROM store starts.
     */
    DirectRead holdForDirectRead() { return DirectRead(_flash); }

    /**
     * NVM-25: memory address of \p length bytes at \p offset of area \p index while \p scope (from
     * holdForDirectRead() of this service) holds the block; 0 otherwise or outside the area.
     */
    uintptr_t areaAddress(DirectRead const& scope, uint32_t const index, uint32_t const offset, uint32_t const length) const
    {
        if ((&scope.flash() != &_flash) || (inArea(index, offset, length) != Status::OK))
        {
            return 0U;
        }
        return scope.address(_areas[index].offset + offset, length);
    }

    /** Starts erasing every sector of area \p index. */
    Status erase(uint32_t const index)
    {
        if (!admitted())
        {
            return Status::NOT_AVAILABLE;
        }
        Status const s = startable(index, 0U, (index < _count) ? _areas[index].size : 0U);
        if (s != Status::OK)
        {
            return s;
        }
        begin(Job::ERASE, _areas[index].offset, _areas[index].offset + _areas[index].size, nullptr);
        return Status::OK;
    }

    /**
     * Starts programming \p length bytes of \p data at \p offset of area \p index. Offset and length are whole
     * program units; the target must be erased (or already hold these bytes). \p data stays valid until the end.
     */
    Status write(uint32_t const index, uint32_t const offset, uint8_t const* const data, uint32_t const length)
    {
        if (!admitted())
        {
            return Status::NOT_AVAILABLE;
        }
        if (data == nullptr)
        {
            return Status::RANGE;
        }
        Status const s = startable(index, offset, length);
        if (s != Status::OK)
        {
            return s;
        }
        uint32_t const unit = _flash.geometry().programSize;
        if (((offset % unit) != 0U) || ((length % unit) != 0U))
        {
            return Status::ALIGNMENT;
        }
        begin(Job::WRITE, _areas[index].offset + offset, _areas[index].offset + offset + length, data);
        return Status::OK;
    }

    /** Advances the job by at most one flash command: BUSY while it runs, its result once at its end, else OK. */
    Status cyclic()
    {
        switch (_job)
        {
            case Job::ERASE: return eraseStep();
            case Job::WRITE: return writeStep();
            default: return Status::OK;
        }
    }

    bool busy() const { return _job != Job::NONE; }

    /** Result of the job that ended last (OK before the first one). */
    Status lastResult() const { return _result; }

    /** Flash commands launched since the start. */
    uint32_t commands() const { return _commands; }

    /** NVM-26: jobs refused by the admission (s32k1xx port: requested from STANDBY on). */
    uint32_t refusedJobs() const { return _refusedJobs; }

private:
    enum class Job : uint8_t
    {
        NONE,
        ERASE,
        WRITE
    };

    static bool sameText(char const* a, char const* b)
    {
        if ((a == nullptr) || (b == nullptr))
        {
            return false;
        }
        while ((*a != '\0') && (*a == *b))
        {
            ++a;
            ++b;
        }
        return *a == *b;
    }

    bool admitted()
    {
        if ((_admission != nullptr) && !_admission())
        {
            ++_refusedJobs;
            return false;
        }
        return true;
    }

    Status inArea(uint32_t const index, uint32_t const offset, uint32_t const length) const
    {
        if (index >= _count)
        {
            return Status::RANGE;
        }
        Area const& a = _areas[index];
        if ((length == 0U) || (offset >= a.size) || (length > (a.size - offset)))
        {
            return Status::RANGE;
        }
        return Status::OK;
    }

    Status startable(uint32_t const index, uint32_t const offset, uint32_t const length) const
    {
        if (_job != Job::NONE)
        {
            return Status::BUSY;
        }
        Status const layout = check();
        if (layout != Status::OK)
        {
            return layout;
        }
        return inArea(index, offset, length);
    }

    void begin(Job const job, uint32_t const start, uint32_t const end, uint8_t const* const data)
    {
        _job       = job;
        _launched  = false;
        _verifying = false;
        _position  = start;
        _end       = end;
        _data      = data;
        _dataStart = start;
    }

    void open()
    {
        if (!_open && (_hooks.begin != nullptr) && (_hooks.end != nullptr))
        {
            _open = true;
            _hooks.begin();
        }
    }

    void close()
    {
        if (_open)
        {
            _open = false;
            _hooks.end();
        }
    }

    Status finish(Status const result)
    {
        _job      = Job::NONE;
        _launched = false;
        _data     = nullptr;
        _result   = result;
        close();
        return result;
    }

    Status eraseStep()
    {
        if (_launched)
        {
            Status const s = _flash.poll();
            if (s == Status::BUSY)
            {
                return Status::BUSY;
            }
            close();
            if (s != Status::OK)
            {
                return finish(s);
            }
            _launched = false;
            _position += _flash.geometry().sectorSize;
            return (_position >= _end) ? finish(Status::OK) : Status::BUSY;
        }
        open();
        Status const s = _flash.startErase(_position);
        if (s != Status::OK)
        {
            close();
        }
        if (s == Status::BUSY)
        {
            return Status::BUSY; // another command runs on the block (an EEPROM store): next step
        }
        if (s != Status::OK)
        {
            return finish(s);
        }
        ++_commands;
        _launched = true;
        return Status::BUSY;
    }

    Status writeStep()
    {
        uint32_t const unit        = _flash.geometry().programSize;
        uint8_t const* const wants = &_data[_position - _dataStart];
        if (_launched)
        {
            Status const s = _flash.poll();
            if (s == Status::BUSY)
            {
                return Status::BUSY;
            }
            close();
            if (s != Status::OK)
            {
                return finish(s);
            }
            _launched  = false;
            _verifying = true;
        }
        Status const r = _flash.read(_position, _unit, unit);
        if (r == Status::BUSY)
        {
            return Status::BUSY;
        }
        if (r != Status::OK)
        {
            return finish(r);
        }
        if (same(_unit, wants, unit))
        {
            _verifying = false; // programmed and read back, or already there
            _position += unit;
            return (_position >= _end) ? finish(Status::OK) : Status::BUSY;
        }
        if (_verifying)
        {
            return finish(Status::FAILED); // read-back differs from what was programmed
        }
        if (!erased(_unit, unit))
        {
            return finish(Status::NOT_ERASED);
        }
        open();
        Status const s = _flash.startProgram(_position, wants);
        if (s != Status::OK)
        {
            close();
        }
        if (s == Status::BUSY)
        {
            return Status::BUSY;
        }
        if (s != Status::OK)
        {
            return finish(s);
        }
        ++_commands;
        _launched = true;
        return Status::BUSY;
    }

    static bool same(uint8_t const* const a, uint8_t const* const b, uint32_t const n)
    {
        for (uint32_t i = 0U; i < n; ++i)
        {
            if (a[i] != b[i])
            {
                return false;
            }
        }
        return true;
    }

    static bool erased(uint8_t const* const a, uint32_t const n)
    {
        for (uint32_t i = 0U; i < n; ++i)
        {
            if (a[i] != 0xFFU)
            {
                return false;
            }
        }
        return true;
    }

    IDataFlash& _flash;
    Area const* const _areas;
    uint32_t const _count;
    WriteHooks const _hooks;
    JobAdmission const _admission;
    Job _job;
    bool _launched;
    bool _verifying;
    bool _open; // a write hook is open (SYS-44)
    Status _result;
    uint32_t _position; // offset in the usable data flash of the sector / program unit in work
    uint32_t _end;
    uint8_t const* _data;
    uint32_t _dataStart;
    uint32_t _commands;
    uint32_t _refusedJobs;
    uint8_t _unit[MAX_PROGRAM_SIZE];
};

} // namespace dataflash
