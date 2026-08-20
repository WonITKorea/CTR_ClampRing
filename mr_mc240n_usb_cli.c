/*
 * Headless MR-MC240 USB controller.
 *
 * Build target: 32-bit Windows.  The 2011 Position Board USB driver works
 * through mc2xxstd_wow64.dll; the 64-bit DLL returns 0x20000010 on this host.
 * No import .lib is required because all vendor functions are resolved with
 * LoadLibrary/GetProcAddress.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <time.h>

typedef int (__stdcall *usb_open_fn)(int, int);
typedef int (__stdcall *usb_close_fn)(int, int);
typedef int (__stdcall *usb_check_fn)(int, int, int *);
typedef int (__stdcall *usb_get_char_fn)(int, int, uint32_t, uint8_t *);
typedef int (__stdcall *usb_get_short_fn)(int, int, uint32_t, uint16_t *);
typedef int (__stdcall *usb_get_long_fn)(int, int, uint32_t, uint32_t *);
typedef int (__stdcall *usb_get_block_fn)(int, int, uint32_t, int, void *);
typedef int (__stdcall *usb_set_char_fn)(int, int, uint32_t, uint8_t);
typedef int (__stdcall *usb_set_short_fn)(int, int, uint32_t, uint16_t);
typedef int (__stdcall *usb_set_long_fn)(int, int, uint32_t, uint32_t);
typedef int (__stdcall *usb_set_block_fn)(int, int, uint32_t, int, const void *);
typedef int (__stdcall *usb_cmd_data_fn)(
    int, int, uint8_t, uint8_t, int, const void *, int, void *);
typedef int (__stdcall *get_last_error_fn)(void);

typedef struct {
    HMODULE module;
    usb_open_fn open;
    usb_close_fn close;
    usb_check_fn check;
    usb_get_char_fn get_char;
    usb_get_short_fn get_short;
    usb_get_long_fn get_long;
    usb_get_block_fn get_block;
    usb_set_char_fn set_char;
    usb_set_short_fn set_short;
    usb_set_long_fn set_long;
    usb_set_block_fn set_block;
    usb_cmd_data_fn cmd_data;
    get_last_error_fn get_last_error;
} UsbApi;

typedef struct {
    int board;
    int channel;
    int opened;
    UsbApi api;
} Controller;

#define USB_MEMORY_BASE             0xB4000000u
#define USB_SYSTEM_STATUS           (USB_MEMORY_BASE + 0x0470u)
#define USB_AXIS_COMMAND_OFFSET     0x1000u
#define USB_AXIS_STATUS_OFFSET      0x1060u
#define USB_AXIS_STRIDE             0x00C0u
#define USB_CHANNEL_STRIDE          0x10000u
#define USB_FAST_POSITION_OFFSET    0xA004u
#define USB_FAST_AXIS_STRIDE        0x0020u
#define PARAM_AXIS_CONTROL_BASE     0x0822B8FCu
#define PARAM_AXIS_STRIDE           0x00000A00u
#define PARAM_CONTROL_BLOCK_SIZE    512

#define AXIS_STATUS_READY           0x01u
#define AXIS_STATUS_SERVO_ALARM     0x20u
#define AXIS_STATUS_OPERATING       0x01u
#define AXIS_STATUS_OPERATION_ALARM 0x20u

#define AXIS_COMMAND_START          0x01u
#define AXIS_COMMAND_DIRECTION      0x02u
#define AXIS_COMMAND_STOP           0x04u
#define AXIS_COMMAND_RAPID_STOP     0x08u

#define AXIS_OPERATION_HOME         0x0002u
#define AXIS_OPERATION_JOG          0x0004u
#define AXIS_OPERATION_INCREMENTAL  0x0008u

#define SAFE_MAX_SPEED              12000u
#define SAFE_MAX_TIME_MS            20000u
#define SAFE_MAX_DISTANCE           100000000

static void json_escape(const char *text)
{
    const unsigned char *p = (const unsigned char *)text;
    putchar('"');
    while (*p) {
        if (*p == '"' || *p == '\\') {
            putchar('\\');
            putchar(*p);
        } else if (*p >= 0x20) {
            putchar(*p);
        }
        ++p;
    }
    putchar('"');
}

static void print_error(const char *operation, int status, uint32_t detail)
{
    printf("{\"ok\":false,\"operation\":");
    json_escape(operation);
    printf(",\"status\":%d,\"detail\":\"0x%08lX\"}\n",
           status, (unsigned long)detail);
}

static FARPROC required_symbol(HMODULE module, const char *name)
{
    FARPROC symbol = GetProcAddress(module, name);
    if (!symbol) {
        fprintf(stderr, "Missing DLL export: %s\n", name);
    }
    return symbol;
}

static HMODULE load_vendor_dll(const char *explicit_path)
{
    HMODULE module;
    char exe_path[MAX_PATH];
    char candidate[MAX_PATH];
    char *slash;

    if (explicit_path && explicit_path[0]) {
        return LoadLibraryA(explicit_path);
    }

    module = LoadLibraryA("vendor\\mitsubishi\\mc2xxstd_wow64.dll");
    if (module) return module;
    module = LoadLibraryA("PbTest\\mc2xxstd_wow64.dll");
    if (module) return module;
    module = LoadLibraryA("mc2xxstd_wow64.dll");
    if (module) return module;

    if (!GetModuleFileNameA(NULL, exe_path, sizeof(exe_path))) return NULL;
    slash = strrchr(exe_path, '\\');
    if (!slash) return NULL;
    *slash = '\0';
    snprintf(candidate, sizeof(candidate),
             "%s\\..\\vendor\\mitsubishi\\mc2xxstd_wow64.dll", exe_path);
    module = LoadLibraryA(candidate);
    if (module) return module;
    snprintf(candidate, sizeof(candidate),
             "%s\\..\\PbTest\\mc2xxstd_wow64.dll", exe_path);
    return LoadLibraryA(candidate);
}

#define LOAD_REQUIRED(field, type, name)                                      \
    do {                                                                      \
        api->field = (type)required_symbol(api->module, name);                \
        if (!api->field) return 0;                                            \
    } while (0)

static int load_api(UsbApi *api, const char *dll_path)
{
    memset(api, 0, sizeof(*api));
    api->module = load_vendor_dll(dll_path);
    if (!api->module) {
        fprintf(stderr, "Could not load mc2xxstd_wow64.dll (Win32 error %lu).\n",
                (unsigned long)GetLastError());
        return 0;
    }
    LOAD_REQUIRED(open, usb_open_fn, "sscUsbOpen");
    LOAD_REQUIRED(close, usb_close_fn, "sscUsbClose");
    LOAD_REQUIRED(check, usb_check_fn, "sscUsbCheckConnect");
    LOAD_REQUIRED(get_char, usb_get_char_fn, "sscUsbGetChar");
    LOAD_REQUIRED(get_short, usb_get_short_fn, "sscUsbGetShort");
    LOAD_REQUIRED(get_long, usb_get_long_fn, "sscUsbGetLong");
    LOAD_REQUIRED(get_block, usb_get_block_fn, "sscUsbGetBlock");
    LOAD_REQUIRED(set_char, usb_set_char_fn, "sscUsbSetChar");
    LOAD_REQUIRED(set_short, usb_set_short_fn, "sscUsbSetShort");
    LOAD_REQUIRED(set_long, usb_set_long_fn, "sscUsbSetLong");
    LOAD_REQUIRED(set_block, usb_set_block_fn, "sscUsbSetBlock");
    LOAD_REQUIRED(cmd_data, usb_cmd_data_fn, "sscUsbCmdData");
    api->get_last_error =
        (get_last_error_fn)GetProcAddress(api->module, "sscGetLastError");
    return 1;
}

static uint32_t api_detail(const UsbApi *api)
{
    if (!api->get_last_error) return 0;
    return (uint32_t)api->get_last_error();
}

static int controller_open(Controller *controller)
{
    int status;
    if (controller->opened) return 1;
    status = controller->api.open(controller->board, controller->channel);
    if (status != 0) {
        print_error("sscUsbOpen", status, api_detail(&controller->api));
        return 0;
    }
    controller->opened = 1;
    return 1;
}

static void controller_close(Controller *controller)
{
    if (controller->opened) {
        controller->api.close(controller->board, controller->channel);
        controller->opened = 0;
    }
}

static int checked_status(Controller *controller)
{
    int status;
    int connected = 0;
    uint8_t signature = 0;
    uint16_t system_status = 0;
    unsigned char identity[5] = {0, 0, 0, 0, 0};

    if (!controller_open(controller)) return 0;
    status = controller->api.cmd_data(
        controller->board, controller->channel, 0x00, 0x0B,
        0, NULL, 4, identity);
    if (status != 0) {
        print_error("sscUsbCmdData", status, api_detail(&controller->api));
        return 0;
    }
    status = controller->api.get_char(
        controller->board, controller->channel, 0xB4000000u, &signature);
    if (status != 0) {
        print_error("sscUsbGetChar", status, api_detail(&controller->api));
        return 0;
    }
    status = controller->api.check(
        controller->board, controller->channel, &connected);
    if (status != 0) {
        print_error("sscUsbCheckConnect", status, api_detail(&controller->api));
        return 0;
    }
    status = controller->api.get_short(
        controller->board, controller->channel, 0xB4000470u, &system_status);
    if (status != 0) {
        print_error("sscUsbGetShort", status, api_detail(&controller->api));
        return 0;
    }

    printf("{\"ok\":true,\"board\":%d,\"channel\":%d,"
           "\"identity\":\"%.4s\",\"signature\":%u,\"connected\":%s,"
           "\"system_status\":%u,\"system_status_hex\":\"0x%04X\"}\n",
           controller->board, controller->channel, identity,
           (unsigned int)signature, connected ? "true" : "false",
           (unsigned int)system_status, (unsigned int)system_status);
    return 1;
}

static int parse_u32(const char *text, uint32_t *value)
{
    char *end = NULL;
    unsigned long parsed = strtoul(text, &end, 0);
    if (!text[0] || !end || *end) return 0;
    *value = (uint32_t)parsed;
    return 1;
}

static int read_value(Controller *controller, const char *kind,
                      const char *address_text)
{
    int status;
    uint32_t address;
    uint32_t value = 0;
    if (!parse_u32(address_text, &address)) {
        fprintf(stderr, "Invalid address: %s\n", address_text);
        return 0;
    }
    if (!controller_open(controller)) return 0;
    if (!strcmp(kind, "read-u8")) {
        uint8_t output = 0;
        status = controller->api.get_char(
            controller->board, controller->channel, address, &output);
        value = output;
    } else if (!strcmp(kind, "read-u16")) {
        uint16_t output = 0;
        status = controller->api.get_short(
            controller->board, controller->channel, address, &output);
        value = output;
    } else {
        status = controller->api.get_long(
            controller->board, controller->channel, address, &value);
    }
    if (status != 0) {
        print_error(kind, status, api_detail(&controller->api));
        return 0;
    }
    printf("{\"ok\":true,\"address\":\"0x%08lX\",\"value\":%lu,"
           "\"value_hex\":\"0x%08lX\"}\n",
           (unsigned long)address, (unsigned long)value, (unsigned long)value);
    return 1;
}

static uint32_t axis_command_address(const Controller *controller, int axis)
{
    return USB_MEMORY_BASE
        + (uint32_t)(controller->channel - 1) * USB_CHANNEL_STRIDE
        + (uint32_t)(axis - 1) * USB_AXIS_STRIDE
        + USB_AXIS_COMMAND_OFFSET;
}

static uint32_t axis_position_address(const Controller *controller, int axis)
{
    return USB_MEMORY_BASE
        + (uint32_t)(controller->channel - 1) * USB_CHANNEL_STRIDE
        + USB_FAST_POSITION_OFFSET
        + (uint32_t)(axis - 1) * USB_FAST_AXIS_STRIDE;
}

static int usb_get_u8(Controller *controller, uint32_t address, uint8_t *value)
{
    int status = controller->api.get_char(
        controller->board, controller->channel, address, value);
    if (status != 0) print_error("sscUsbGetChar", status, api_detail(&controller->api));
    return status == 0;
}

static int usb_get_u16(Controller *controller, uint32_t address, uint16_t *value)
{
    int status = controller->api.get_short(
        controller->board, controller->channel, address, value);
    if (status != 0) print_error("sscUsbGetShort", status, api_detail(&controller->api));
    return status == 0;
}

static int usb_get_u32(Controller *controller, uint32_t address, uint32_t *value)
{
    int status = controller->api.get_long(
        controller->board, controller->channel, address, value);
    if (status != 0) print_error("sscUsbGetLong", status, api_detail(&controller->api));
    return status == 0;
}

static int usb_set_u8(Controller *controller, uint32_t address, uint8_t value)
{
    int status = controller->api.set_char(
        controller->board, controller->channel, address, value);
    if (status != 0) print_error("sscUsbSetChar", status, api_detail(&controller->api));
    return status == 0;
}

static int usb_set_u16(Controller *controller, uint32_t address, uint16_t value)
{
    int status = controller->api.set_short(
        controller->board, controller->channel, address, value);
    if (status != 0) print_error("sscUsbSetShort", status, api_detail(&controller->api));
    return status == 0;
}

static int usb_set_u32(Controller *controller, uint32_t address, uint32_t value)
{
    int status = controller->api.set_long(
        controller->board, controller->channel, address, value);
    if (status != 0) print_error("sscUsbSetLong", status, api_detail(&controller->api));
    return status == 0;
}

static int usb_get_block(
    Controller *controller, uint32_t address, int size, void *data)
{
    int status = controller->api.get_block(
        controller->board, controller->channel, address, size, data);
    if (status != 0) {
        print_error("sscUsbGetBlock", status, api_detail(&controller->api));
    }
    return status == 0;
}

static int usb_set_block(
    Controller *controller, uint32_t address, int size, const void *data)
{
    int status = controller->api.set_block(
        controller->board, controller->channel, address, size, data);
    if (status != 0) {
        print_error("sscUsbSetBlock", status, api_detail(&controller->api));
    }
    return status == 0;
}

static int validate_axis(int axis)
{
    if (axis < 1 || axis > 20) {
        printf("{\"ok\":false,\"error\":\"axis must be 1..20\"}\n");
        return 0;
    }
    return 1;
}

static int read_axis_bytes(
    Controller *controller, int axis, uint8_t *command0, uint8_t *command1,
    uint16_t *operation, uint8_t *status0, uint8_t *status1)
{
    uint32_t address;
    if (!validate_axis(axis) || !controller_open(controller)) return 0;
    address = axis_command_address(controller, axis);
    return usb_get_u8(controller, address, command0)
        && usb_get_u8(controller, address + 1, command1)
        && usb_get_u16(controller, address + 2, operation)
        && usb_get_u8(controller, address + 0x60, status0)
        && usb_get_u8(controller, address + 0x61, status1);
}

static int axis_state(Controller *controller, int axis)
{
    uint8_t command0, command1, status0, status1;
    uint16_t operation;
    uint32_t raw_position;
    if (!read_axis_bytes(
            controller, axis, &command0, &command1, &operation, &status0, &status1))
        return 0;
    if (!usb_get_u32(controller, axis_position_address(controller, axis), &raw_position))
        return 0;
    printf(
        "{\"ok\":true,\"axis\":%d,\"position\":%ld,"
        "\"command0\":%u,\"command1\":%u,\"operation\":%u,"
        "\"status0\":%u,\"status1\":%u,\"servo_ready\":%s,"
        "\"in_position\":%s,\"servo_alarm\":%s,\"operating\":%s,"
        "\"home_complete\":%s,\"operation_alarm\":%s}\n",
        axis, (long)(int32_t)raw_position, (unsigned)command0, (unsigned)command1,
        (unsigned)operation, (unsigned)status0, (unsigned)status1,
        (status0 & AXIS_STATUS_READY) ? "true" : "false",
        (status0 & 0x02u) ? "true" : "false",
        (status0 & AXIS_STATUS_SERVO_ALARM) ? "true" : "false",
        (status1 & AXIS_STATUS_OPERATING) ? "true" : "false",
        (status1 & 0x08u) ? "true" : "false",
        (status1 & AXIS_STATUS_OPERATION_ALARM) ? "true" : "false");
    return 1;
}

static int require_motion_ready(Controller *controller, int axis, uint32_t *address)
{
    uint8_t command0, command1, status0, status1;
    uint16_t operation, system_status;
    if (!read_axis_bytes(
            controller, axis, &command0, &command1, &operation, &status0, &status1))
        return 0;
    if (!usb_get_u16(controller, USB_SYSTEM_STATUS, &system_status)) return 0;
    if ((system_status & 0xE000u) == 0xE000u || system_status == 0x0001u) {
        printf("{\"ok\":false,\"error\":\"system is not started\","
               "\"system_status\":%u}\n", (unsigned)system_status);
        return 0;
    }
    if (!(status0 & AXIS_STATUS_READY)) {
        printf("{\"ok\":false,\"error\":\"axis is not ready\",\"axis\":%d,"
               "\"status0\":%u,\"status1\":%u}\n",
               axis, (unsigned)status0, (unsigned)status1);
        return 0;
    }
    if ((status0 & AXIS_STATUS_SERVO_ALARM)
        || (status1 & AXIS_STATUS_OPERATION_ALARM)) {
        printf("{\"ok\":false,\"error\":\"axis alarm is active\",\"axis\":%d,"
               "\"status0\":%u,\"status1\":%u}\n",
               axis, (unsigned)status0, (unsigned)status1);
        return 0;
    }
    if (status1 & AXIS_STATUS_OPERATING) {
        printf("{\"ok\":false,\"error\":\"axis is already operating\","
               "\"axis\":%d,\"operation\":%u}\n", axis, (unsigned)operation);
        return 0;
    }
    *address = axis_command_address(controller, axis);
    return 1;
}

static int set_servo(Controller *controller, int axis, int enabled)
{
    uint32_t address;
    uint8_t value, status0;
    uint16_t system_status;
    if (!validate_axis(axis) || !controller_open(controller)) return 0;
    if (!usb_get_u16(controller, USB_SYSTEM_STATUS, &system_status)) return 0;
    if ((system_status & 0xE000u) == 0xE000u || system_status == 0x0001u) {
        printf("{\"ok\":false,\"error\":\"system is not started\","
               "\"system_status\":%u}\n", (unsigned)system_status);
        return 0;
    }
    address = axis_command_address(controller, axis);
    if (!usb_get_u8(controller, address, &value)
        || !usb_get_u8(controller, address + 0x60, &status0))
        return 0;
    if (status0 == 0 && enabled) {
        printf("{\"ok\":false,\"error\":\"axis is not mounted or configured\","
               "\"axis\":%d}\n", axis);
        return 0;
    }
    value = enabled ? (uint8_t)(value | 1u) : (uint8_t)(value & ~1u);
    if (!usb_set_u8(controller, address, value)) return 0;
    printf("{\"ok\":true,\"axis\":%d,\"servo_command\":%s}\n",
           axis, enabled ? "true" : "false");
    return 1;
}

static int start_motion(
    Controller *controller, int axis, uint16_t operation, int direction,
    int32_t distance, uint32_t speed, uint16_t acceleration, uint16_t deceleration)
{
    uint32_t address;
    uint8_t command1;
    if (speed < 1 || speed > SAFE_MAX_SPEED
        || acceleration > SAFE_MAX_TIME_MS || deceleration > SAFE_MAX_TIME_MS) {
        printf("{\"ok\":false,\"error\":\"motion value exceeds safety limit\"}\n");
        return 0;
    }
    if (operation == AXIS_OPERATION_INCREMENTAL
        && (distance == 0 || distance > SAFE_MAX_DISTANCE
            || distance < -SAFE_MAX_DISTANCE)) {
        printf("{\"ok\":false,\"error\":\"distance exceeds safety limit\"}\n");
        return 0;
    }
    if (!require_motion_ready(controller, axis, &address)) return 0;
    if (!usb_get_u8(controller, address + 1, &command1)) return 0;
    command1 = direction
        ? (uint8_t)(command1 | AXIS_COMMAND_DIRECTION)
        : (uint8_t)(command1 & ~AXIS_COMMAND_DIRECTION);
    if (!usb_set_u32(controller, address + 0x20, speed)
        || !usb_set_u16(controller, address + 0x24, acceleration)
        || !usb_set_u16(controller, address + 0x26, deceleration))
        return 0;
    if (operation == AXIS_OPERATION_INCREMENTAL
        && !usb_set_u32(controller, address + 0x28, (uint32_t)abs(distance)))
        return 0;
    if (!usb_set_u8(controller, address + 1, command1)
        || !usb_set_u16(controller, address + 2, operation)
        || !usb_set_u8(controller, address + 1,
                       (uint8_t)(command1 | AXIS_COMMAND_START)))
        return 0;
    printf("{\"ok\":true,\"axis\":%d,\"operation\":%u}\n",
           axis, (unsigned)operation);
    return 1;
}

static int start_home(Controller *controller, int axis)
{
    uint32_t address;
    uint8_t command1;
    if (!require_motion_ready(controller, axis, &address)) return 0;
    if (!usb_get_u8(controller, address + 1, &command1)
        || !usb_set_u16(controller, address + 2, AXIS_OPERATION_HOME)
        || !usb_set_u8(controller, address + 1,
                       (uint8_t)(command1 | AXIS_COMMAND_START)))
        return 0;
    printf("{\"ok\":true,\"axis\":%d,\"operation\":%u}\n",
           axis, AXIS_OPERATION_HOME);
    return 1;
}

static int stop_motion(Controller *controller, int axis, int rapid)
{
    uint32_t address;
    uint8_t command1, status1;
    uint16_t operation;
    int elapsed = 0;
    if (!validate_axis(axis) || !controller_open(controller)) return 0;
    address = axis_command_address(controller, axis);
    if (!usb_get_u8(controller, address + 1, &command1)
        || !usb_get_u8(controller, address + 0x61, &status1)
        || !usb_get_u16(controller, address + 2, &operation))
        return 0;
    if (operation == AXIS_OPERATION_JOG) {
        command1 = (uint8_t)(command1 & ~AXIS_COMMAND_START);
        if (!usb_set_u8(controller, address + 1, command1)) return 0;
    } else if (status1 & AXIS_STATUS_OPERATING) {
        uint8_t mask = rapid ? AXIS_COMMAND_RAPID_STOP : AXIS_COMMAND_STOP;
        if (!usb_set_u8(controller, address + 1, (uint8_t)(command1 | mask)))
            return 0;
        do {
            Sleep(10);
            elapsed += 10;
            if (!usb_get_u8(controller, address + 0x61, &status1)) return 0;
        } while ((status1 & AXIS_STATUS_OPERATING) && elapsed < 3000);
        if (!usb_set_u8(controller, address + 1, (uint8_t)(command1 & ~mask)))
            return 0;
    }
    printf("{\"ok\":true,\"axis\":%d,\"stopped\":true,\"rapid\":%s}\n",
           axis, rapid ? "true" : "false");
    return 1;
}

static int send_system_start(Controller *controller, uint16_t *status_code)
{
    uint64_t wall_clock = (uint64_t)time(NULL);
    LARGE_INTEGER counter;
    if (!controller_open(controller)) return 0;
    QueryPerformanceCounter(&counter);
    if (!usb_set_u32(controller, USB_MEMORY_BASE + 0x0448u,
                     (uint32_t)wall_clock)
        || !usb_set_u32(controller, USB_MEMORY_BASE + 0x044Cu,
                        (uint32_t)(wall_clock >> 32))
        || !usb_set_u32(controller, USB_MEMORY_BASE + 0x0778u,
                        (uint32_t)counter.QuadPart)
        || !usb_set_u32(controller, USB_MEMORY_BASE + 0x077Cu,
                        (uint32_t)((uint64_t)counter.QuadPart >> 32))
        || !usb_set_u16(controller, USB_MEMORY_BASE + 0x0400u, 0x000Au))
        return 0;
    Sleep(100);
    return usb_get_u16(controller, USB_SYSTEM_STATUS, status_code);
}

static int system_start(Controller *controller)
{
    uint16_t status_code = 0;
    if (!send_system_start(controller, &status_code)) return 0;
    printf("{\"ok\":true,\"system_start\":true,\"system_status\":%u,"
           "\"system_status_hex\":\"0x%04X\"}\n",
           (unsigned)status_code, (unsigned)status_code);
    return 1;
}

static int all_axes_idle_and_servo_off(Controller *controller)
{
    int axis;
    for (axis = 1; axis <= 20; ++axis) {
        uint8_t command0, command1, status0, status1;
        uint16_t operation;
        if (!read_axis_bytes(
                controller, axis, &command0, &command1, &operation,
                &status0, &status1))
            return 0;
        if ((command0 & 0x01u) || (status1 & AXIS_STATUS_OPERATING)) {
            printf("{\"ok\":false,\"error\":\"axis must be servo-off and idle "
                   "before configuration\",\"axis\":%d}\n", axis);
            return 0;
        }
    }
    return 1;
}

static int write_btk1404_six_axis_parameters(Controller *controller)
{
    int axis;
    for (axis = 1; axis <= 20; ++axis) {
        uint16_t block[PARAM_CONTROL_BLOCK_SIZE / 2];
        uint32_t address = PARAM_AXIS_CONTROL_BASE
            + (uint32_t)(axis - 1) * PARAM_AXIS_STRIDE;
        if (!usb_get_block(
                controller, address, PARAM_CONTROL_BLOCK_SIZE, block))
            return 0;

        block[0x00] = axis <= 6 ? 0x0001u : 0x0000u; /* 0200 OPC1 */
        if (axis <= 6) {
            /* Pr.0203: 0 means unassigned; amplifier axes are numbered 1..20. */
            block[0x03] = (uint16_t)axis;
            /* Pr.0219: no external LSP/LSN/DOG; both limits are invalid. */
            block[0x19] = 0x0303u;
            block[0x0A] = 0x0000u; /* 020A CMX lower */
            block[0x0B] = 0x0040u; /* 020B CMX upper: 4194304 */
            block[0x0C] = 0x0FA0u; /* 020C CDV lower: 4000 um/rev */
            block[0x0D] = 0x0000u; /* 020D CDV upper */
            block[0x0E] = 0x03E8u; /* 020E speed factor: 1000 */
            block[0x0F] = 0x0000u; /* 020F speed factor upper */
            block[0x1E] = 0x1000u; /* 021E MR-J4 type code */
        }
        if (!usb_set_block(
                controller, address, PARAM_CONTROL_BLOCK_SIZE, block))
            return 0;
    }
    return 1;
}

static int software_reboot(Controller *controller)
{
    uint8_t command;
    uint8_t reboot_status = 0;
    int elapsed = 0;

    if (!usb_set_u16(
            controller, USB_MEMORY_BASE + 0x0406u, 0x1EA5u)
        || !usb_get_u8(
            controller, USB_MEMORY_BASE + 0x03E8u, &command))
        return 0;

    command = (uint8_t)(command & ~0x03u);
    if (!usb_set_u8(
            controller, USB_MEMORY_BASE + 0x03E8u,
            (uint8_t)(command | 0x01u)))
        return 0;

    while (elapsed < 5000) {
        if (!usb_get_u8(
                controller, USB_MEMORY_BASE + 0x0458u, &reboot_status))
            return 0;
        if (reboot_status & 0x02u) {
            printf("{\"ok\":false,\"error\":\"reboot preparation failed\","
                   "\"reboot_status\":%u}\n", (unsigned)reboot_status);
            return 0;
        }
        if (reboot_status & 0x01u) break;
        Sleep(20);
        elapsed += 20;
    }
    if (!(reboot_status & 0x01u)) {
        printf("{\"ok\":false,\"error\":\"reboot preparation timed out\"}\n");
        return 0;
    }
    if (!usb_set_u8(
            controller, USB_MEMORY_BASE + 0x03E8u,
            (uint8_t)(command | 0x03u)))
        return 0;

    controller_close(controller);
    Sleep(1000);
    elapsed = 0;
    while (elapsed < 10000) {
        int status = controller->api.open(
            controller->board, controller->channel);
        if (status == 0) {
            controller->opened = 1;
            return 1;
        }
        Sleep(250);
        elapsed += 250;
    }
    printf("{\"ok\":false,\"error\":\"USB did not reconnect after reboot\","
           "\"detail\":\"0x%08lX\"}\n",
           (unsigned long)api_detail(&controller->api));
    return 0;
}

static int configure_btk1404_six_axes(Controller *controller)
{
    uint16_t status_code = 0;
    int elapsed = 0;
    int mounted_axes = 0;
    int axis;

    if (!controller_open(controller) || !all_axes_idle_and_servo_off(controller))
        return 0;
    if (!software_reboot(controller)) return 0;
    /*
     * Software reboot restores the parameter area from flash.  Write the
     * application parameters after status 0001h and before command 000Ah.
     */
    if (!write_btk1404_six_axis_parameters(controller)) return 0;
    if (!send_system_start(controller, &status_code)) return 0;

    while (elapsed < 10000 && status_code != 0x000Au
           && (status_code & 0xE000u) != 0xE000u) {
        Sleep(50);
        elapsed += 50;
        if (!usb_get_u16(controller, USB_SYSTEM_STATUS, &status_code)) return 0;
    }
    if (status_code != 0x000Au) {
        if (status_code == 0x0009u) {
            printf("{\"ok\":false,"
                   "\"error\":\"waiting for SSCNET response from amplifiers\","
                   "\"system_status\":9,\"system_status_hex\":\"0x0009\","
                   "\"parameters_written\":true,"
                   "\"check\":\"controller to first amplifier CN1A; each CN1B "
                   "to next CN1A; amplifier control power and axis switches\"}\n");
        } else {
            printf("{\"ok\":false,\"error\":\"six-axis system startup failed\","
                   "\"system_status\":%u,\"system_status_hex\":\"0x%04X\","
                   "\"parameters_written\":true}\n",
                   (unsigned)status_code, (unsigned)status_code);
        }
        return 0;
    }
    for (axis = 1; axis <= 6; ++axis) {
        uint8_t command0, command1, status0, status1;
        uint16_t operation;
        if (!read_axis_bytes(
                controller, axis, &command0, &command1, &operation,
                &status0, &status1))
            return 0;
        if (status0 != 0 || status1 != 0) ++mounted_axes;
    }
    if (mounted_axes != 6) {
        printf("{\"ok\":false,\"error\":\"six-axis parameters were written "
               "but not all amplifiers mounted\",\"mounted_axes\":%d,"
               "\"system_status\":%u,\"restart_required\":true}\n",
               mounted_axes, (unsigned)status_code);
        return 0;
    }
    printf("{\"ok\":true,\"configured\":true,\"axes\":6,"
           "\"motor\":\"HG-KR13\",\"ball_screw\":\"BTK1404\","
           "\"command_units_per_mm\":1000,\"system_status\":%u,"
           "\"system_status_hex\":\"0x%04X\"}\n",
           (unsigned)status_code, (unsigned)status_code);
    return 1;
}

static int parse_i32_text(const char *text_value, int32_t *value)
{
    char *end = NULL;
    long parsed = strtol(text_value, &end, 0);
    if (!text_value[0] || !end || *end) return 0;
    *value = (int32_t)parsed;
    return 1;
}

static int serve(Controller *controller)
{
    char line[512];
    if (!controller_open(controller)) return 0;
    setvbuf(stdout, NULL, _IOLBF, 0);
    printf("{\"ok\":true,\"event\":\"ready\",\"board\":%d,\"channel\":%d}\n",
           controller->board, controller->channel);
    fflush(stdout);
    while (fgets(line, sizeof(line), stdin)) {
        char *argv_line[10];
        int argc_line = 0;
        char *token = strtok(line, " \t\r\n");
        while (token && argc_line < 10) {
            argv_line[argc_line++] = token;
            token = strtok(NULL, " \t\r\n");
        }
        if (!argc_line) continue;
        if (!strcmp(argv_line[0], "QUIT")) {
            printf("{\"ok\":true,\"event\":\"bye\"}\n");
            return 1;
        } else if (!strcmp(argv_line[0], "STATUS")) {
            checked_status(controller);
        } else if (!strcmp(argv_line[0], "AXIS_STATE") && argc_line == 2) {
            axis_state(controller, atoi(argv_line[1]));
        } else if (!strcmp(argv_line[0], "SERVO") && argc_line == 3) {
            set_servo(controller, atoi(argv_line[1]), atoi(argv_line[2]) != 0);
        } else if (!strcmp(argv_line[0], "SYSTEM_START") && argc_line == 1) {
            system_start(controller);
        } else if (!strcmp(argv_line[0], "CONFIGURE_6AXES_BTK1404")
                   && argc_line == 1) {
            configure_btk1404_six_axes(controller);
        } else if (!strcmp(argv_line[0], "JOG") && argc_line == 6) {
            uint32_t speed, acceleration, deceleration;
            if (parse_u32(argv_line[3], &speed)
                && parse_u32(argv_line[4], &acceleration)
                && parse_u32(argv_line[5], &deceleration)) {
                start_motion(controller, atoi(argv_line[1]), AXIS_OPERATION_JOG,
                             atoi(argv_line[2]) != 0, 0, speed,
                             (uint16_t)acceleration, (uint16_t)deceleration);
            } else printf("{\"ok\":false,\"error\":\"invalid JOG arguments\"}\n");
        } else if (!strcmp(argv_line[0], "MOVE_RELATIVE") && argc_line == 6) {
            int32_t distance;
            uint32_t speed, acceleration, deceleration;
            if (parse_i32_text(argv_line[2], &distance)
                && parse_u32(argv_line[3], &speed)
                && parse_u32(argv_line[4], &acceleration)
                && parse_u32(argv_line[5], &deceleration)) {
                start_motion(controller, atoi(argv_line[1]),
                             AXIS_OPERATION_INCREMENTAL, distance < 0,
                             distance, speed, (uint16_t)acceleration,
                             (uint16_t)deceleration);
            } else printf("{\"ok\":false,\"error\":\"invalid MOVE arguments\"}\n");
        } else if (!strcmp(argv_line[0], "HOME") && argc_line == 2) {
            start_home(controller, atoi(argv_line[1]));
        } else if (!strcmp(argv_line[0], "STOP") && argc_line == 2) {
            stop_motion(controller, atoi(argv_line[1]), 0);
        } else if (!strcmp(argv_line[0], "RAPID_STOP") && argc_line == 2) {
            stop_motion(controller, atoi(argv_line[1]), 1);
        } else {
            printf("{\"ok\":false,\"error\":\"unknown command\"}\n");
        }
        fflush(stdout);
    }
    return 1;
}

static void usage(const char *program)
{
    fprintf(stderr,
        "Usage: %s [--board 0..3] [--channel 1..2] [--dll PATH] COMMAND\n"
        "Commands:\n"
        "  status                  Open, verify, report, and close USB\n"
        "  read-u8 ADDRESS         Read one confirmed/known memory address\n"
        "  read-u16 ADDRESS        Read a 16-bit memory address\n"
        "  read-u32 ADDRESS        Read a 32-bit memory address\n"
        "  serve                   Persistent JSON line control bridge\n",
        program);
}

int main(int argc, char **argv)
{
    Controller controller;
    const char *dll_path = NULL;
    const char *command = NULL;
    const char *command_arg = NULL;
    int index;
    int ok = 0;

    memset(&controller, 0, sizeof(controller));
    controller.board = 0;
    controller.channel = 1;

    for (index = 1; index < argc; ++index) {
        if (!strcmp(argv[index], "--board") && index + 1 < argc) {
            controller.board = atoi(argv[++index]);
        } else if (!strcmp(argv[index], "--channel") && index + 1 < argc) {
            controller.channel = atoi(argv[++index]);
        } else if (!strcmp(argv[index], "--dll") && index + 1 < argc) {
            dll_path = argv[++index];
        } else if (!command) {
            command = argv[index];
        } else if (!command_arg) {
            command_arg = argv[index];
        } else {
            usage(argv[0]);
            return 2;
        }
    }
    if (!command || controller.board < 0 || controller.board > 3 ||
        controller.channel < 1 || controller.channel > 2) {
        usage(argv[0]);
        return 2;
    }
    if (!load_api(&controller.api, dll_path)) return 3;

    if (!strcmp(command, "status")) {
        ok = checked_status(&controller);
    } else if (!strcmp(command, "serve")) {
        ok = serve(&controller);
    } else if ((!strcmp(command, "read-u8") ||
                !strcmp(command, "read-u16") ||
                !strcmp(command, "read-u32")) && command_arg) {
        ok = read_value(&controller, command, command_arg);
    } else {
        usage(argv[0]);
    }

    controller_close(&controller);
    if (controller.api.module) FreeLibrary(controller.api.module);
    return ok ? 0 : 1;
}
