"""
Curated signatures for common Windows and CRT functions.

Used to give the AI ground-truth context when it encounters well-known imports,
instead of having it guess parameter names and types from usage alone.

Format:  name -> (signature, one-line description)
"""

KNOWN_APIS: dict[str, tuple[str, str]] = {

    # ------------------------------------------------------------------ #
    # CRT startup / initialization
    # ------------------------------------------------------------------ #
    "__set_app_type": (
        "void __set_app_type(int app_type)",
        "Set CRT app type: 1=console, 2=GUI — must be called before _initterm",
    ),
    "__getmainargs": (
        "int __getmainargs(int *argc, char ***argv, char ***envp, int dowildcard, _startupinfo *startinfo)",
        "Parse command-line into argc/argv/envp; dowildcard expands wildcards",
    ),
    "__wgetmainargs": (
        "int __wgetmainargs(int *argc, wchar_t ***argv, wchar_t ***envp, int dowildcard, _startupinfo *startinfo)",
        "Wide-char version of __getmainargs",
    ),
    "_initterm": (
        "void _initterm(void (**start)(void), void (**end)(void))",
        "Call each non-null function pointer in [start, end) — runs static C++ initializers",
    ),
    "_initterm_e": (
        "int _initterm_e(int (**start)(void), int (**end)(void))",
        "Like _initterm but propagates non-zero return code — stops on first failure",
    ),
    "_amsg_exit": (
        "void _amsg_exit(int rterrnum)",
        "Display CRT runtime-error message and call _exit — fatal, no cleanup",
    ),
    "_cexit": (
        "void _cexit(void)",
        "Run atexit handlers and flush streams without terminating the process",
    ),
    "_exit": (
        "void _exit(int status)",
        "Terminate immediately — no atexit handlers, no stream flush",
    ),
    "exit": (
        "void exit(int status)",
        "Terminate after running atexit handlers and flushing all open streams",
    ),
    "_IsNonwritableInCurrentImage": (
        "BOOL _IsNonwritableInCurrentImage(PBYTE pTarget)",
        "Return TRUE if pTarget is in a read-only section of the loaded image — used to validate function pointers",
    ),
    "__setusermatherr": (
        "void __setusermatherr(int (*handler)(struct _exception *))",
        "Register a custom _matherr handler for math exceptions",
    ),

    # ------------------------------------------------------------------ #
    # CRT memory
    # ------------------------------------------------------------------ #
    "malloc":   ("void* malloc(size_t size)",                           "Allocate uninitialized heap memory"),
    "calloc":   ("void* calloc(size_t count, size_t size)",             "Allocate zero-initialized heap memory"),
    "realloc":  ("void* realloc(void* ptr, size_t new_size)",           "Resize heap allocation"),
    "free":     ("void  free(void* ptr)",                               "Free heap memory"),
    "_msize":   ("size_t _msize(void* ptr)",                            "Return size of heap allocation"),
    "memcpy":   ("void* memcpy(void* dst, const void* src, size_t n)",  "Copy n bytes (no overlap)"),
    "memmove":  ("void* memmove(void* dst, const void* src, size_t n)", "Copy n bytes (overlap-safe)"),
    "memset":   ("void* memset(void* dst, int c, size_t n)",            "Fill n bytes with c"),
    "memcmp":   ("int   memcmp(const void* a, const void* b, size_t n)","Compare n bytes; <0 / 0 / >0"),
    "memchr":   ("void* memchr(const void* s, int c, size_t n)",        "Find first occurrence of byte c in n bytes"),

    # ------------------------------------------------------------------ #
    # CRT string (narrow)
    # ------------------------------------------------------------------ #
    "strlen":   ("size_t strlen(const char* s)",                               "Null-terminated string length"),
    "strcpy":   ("char*  strcpy(char* dst, const char* src)",                  "Copy string (no bounds check)"),
    "strncpy":  ("char*  strncpy(char* dst, const char* src, size_t n)",       "Copy up to n chars"),
    "strcat":   ("char*  strcat(char* dst, const char* src)",                  "Concatenate strings"),
    "strncat":  ("char*  strncat(char* dst, const char* src, size_t n)",       "Append up to n chars"),
    "strcmp":   ("int    strcmp(const char* a, const char* b)",                "Compare strings; <0/0/>0"),
    "strncmp":  ("int    strncmp(const char* a, const char* b, size_t n)",     "Compare up to n chars"),
    "strstr":   ("char*  strstr(const char* haystack, const char* needle)",    "Find first occurrence of needle in haystack"),
    "strchr":   ("char*  strchr(const char* s, int c)",                        "Find first occurrence of char c"),
    "strrchr":  ("char*  strrchr(const char* s, int c)",                       "Find last occurrence of char c"),
    "strtol":   ("long   strtol(const char* s, char** endptr, int base)",      "Parse string to long"),
    "strtoll":  ("long long strtoll(const char* s, char** endptr, int base)",  "Parse string to long long"),
    "strtoul":  ("unsigned long strtoul(const char* s, char** endptr, int base)", "Parse string to unsigned long"),
    "atoi":     ("int    atoi(const char* s)",                                 "Parse string to int"),
    "atof":     ("double atof(const char* s)",                                 "Parse string to double"),

    # ------------------------------------------------------------------ #
    # CRT string (wide)
    # ------------------------------------------------------------------ #
    "wcslen":   ("size_t   wcslen(const wchar_t* s)",                          "Wide string length"),
    "wcscpy":   ("wchar_t* wcscpy(wchar_t* dst, const wchar_t* src)",          "Copy wide string"),
    "wcscmp":   ("int      wcscmp(const wchar_t* a, const wchar_t* b)",        "Compare wide strings"),
    "wcscat":   ("wchar_t* wcscat(wchar_t* dst, const wchar_t* src)",          "Concatenate wide strings"),

    # ------------------------------------------------------------------ #
    # CRT formatted I/O
    # ------------------------------------------------------------------ #
    "printf":   ("int printf(const char* fmt, ...)",                           "Print formatted to stdout"),
    "fprintf":  ("int fprintf(FILE* stream, const char* fmt, ...)",            "Print formatted to stream"),
    "sprintf":  ("int sprintf(char* buf, const char* fmt, ...)",               "Format string into buffer (no bounds check)"),
    "snprintf": ("int snprintf(char* buf, size_t n, const char* fmt, ...)",    "Format string into buffer (bounded)"),
    "sscanf":   ("int sscanf(const char* str, const char* fmt, ...)",          "Parse formatted string"),
    "vprintf":  ("int vprintf(const char* fmt, va_list args)",                 "printf via va_list"),
    "vsprintf": ("int vsprintf(char* buf, const char* fmt, va_list args)",     "sprintf via va_list"),
    "vsnprintf":("int vsnprintf(char* buf, size_t n, const char* fmt, va_list args)", "snprintf via va_list"),

    # ------------------------------------------------------------------ #
    # CRT file I/O
    # ------------------------------------------------------------------ #
    "fopen":    ("FILE* fopen(const char* path, const char* mode)",            "Open file; mode: r/w/a/rb/wb"),
    "fclose":   ("int   fclose(FILE* stream)",                                 "Close file stream"),
    "fread":    ("size_t fread(void* buf, size_t size, size_t count, FILE* stream)",  "Read count elements of size bytes each"),
    "fwrite":   ("size_t fwrite(const void* buf, size_t size, size_t count, FILE* stream)", "Write count elements"),
    "fgets":    ("char*  fgets(char* buf, int n, FILE* stream)",               "Read line (up to n-1 chars)"),
    "fputs":    ("int    fputs(const char* s, FILE* stream)",                  "Write string to stream"),
    "fseek":    ("int    fseek(FILE* stream, long offset, int origin)",        "Seek in stream; origin: SEEK_SET/CUR/END"),
    "ftell":    ("long   ftell(FILE* stream)",                                 "Get current stream position"),
    "feof":     ("int    feof(FILE* stream)",                                  "Test end-of-file indicator"),
    "fflush":   ("int    fflush(FILE* stream)",                                "Flush stream write buffer"),
    "remove":   ("int    remove(const char* path)",                            "Delete file"),
    "rename":   ("int    rename(const char* old, const char* new_name)",       "Rename/move file"),

    # ------------------------------------------------------------------ #
    # CRT math
    # ------------------------------------------------------------------ #
    "sqrt":   ("double sqrt(double x)",            "Square root"),
    "sqrtf":  ("float  sqrtf(float x)",            "Square root (float)"),
    "pow":    ("double pow(double base, double exp)", "base raised to exp"),
    "abs":    ("int    abs(int x)",                "Absolute value (int)"),
    "fabs":   ("double fabs(double x)",            "Absolute value (double)"),
    "floor":  ("double floor(double x)",           "Round toward -inf"),
    "ceil":   ("double ceil(double x)",            "Round toward +inf"),
    "round":  ("double round(double x)",           "Round to nearest, halfway away from zero"),
    "log":    ("double log(double x)",             "Natural logarithm"),
    "log2":   ("double log2(double x)",            "Base-2 logarithm"),
    "log10":  ("double log10(double x)",           "Base-10 logarithm"),
    "sin":    ("double sin(double x)",             "Sine (radians)"),
    "cos":    ("double cos(double x)",             "Cosine (radians)"),
    "tan":    ("double tan(double x)",             "Tangent (radians)"),

    # ------------------------------------------------------------------ #
    # Windows — process / thread
    # ------------------------------------------------------------------ #
    "ExitProcess":        ("void  ExitProcess(UINT uExitCode)",                      "Terminate process and all threads"),
    "GetCurrentProcessId":("DWORD GetCurrentProcessId(void)",                        "Return current process ID"),
    "GetCurrentThreadId": ("DWORD GetCurrentThreadId(void)",                         "Return current thread ID"),
    "Sleep":              ("void  Sleep(DWORD dwMilliseconds)",                       "Suspend calling thread for dwMilliseconds"),
    "GetLastError":       ("DWORD GetLastError(void)",                               "Return thread-local last Win32 error code"),
    "SetLastError":       ("void  SetLastError(DWORD dwErrCode)",                    "Set thread-local last Win32 error code"),
    "GetTickCount":       ("DWORD GetTickCount(void)",                               "Milliseconds since system start (wraps ~49 days)"),
    "GetTickCount64":     ("ULONGLONG GetTickCount64(void)",                         "64-bit milliseconds since system start"),

    # ------------------------------------------------------------------ #
    # Windows — heap
    # ------------------------------------------------------------------ #
    "GetProcessHeap": ("HANDLE HeapHandle GetProcessHeap(void)",                      "Get default process heap handle"),
    "HeapAlloc":      ("LPVOID HeapAlloc(HANDLE hHeap, DWORD dwFlags, SIZE_T dwBytes)", "Allocate from heap; HEAP_ZERO_MEMORY=8"),
    "HeapFree":       ("BOOL   HeapFree(HANDLE hHeap, DWORD dwFlags, LPVOID lpMem)",  "Free heap allocation"),
    "HeapCreate":     ("HANDLE HeapCreate(DWORD flOptions, SIZE_T dwInitSize, SIZE_T dwMaxSize)", "Create private heap"),
    "HeapDestroy":    ("BOOL   HeapDestroy(HANDLE hHeap)",                            "Destroy private heap"),
    "HeapReAlloc":    ("LPVOID HeapReAlloc(HANDLE hHeap, DWORD dwFlags, LPVOID lpMem, SIZE_T dwBytes)", "Resize heap allocation"),
    "HeapSize":       ("SIZE_T HeapSize(HANDLE hHeap, DWORD dwFlags, LPCVOID lpMem)", "Return size of heap block"),

    # ------------------------------------------------------------------ #
    # Windows — virtual memory
    # ------------------------------------------------------------------ #
    "VirtualAlloc":   ("LPVOID VirtualAlloc(LPVOID lpAddress, SIZE_T dwSize, DWORD flAllocationType, DWORD flProtect)", "Reserve/commit virtual pages"),
    "VirtualFree":    ("BOOL   VirtualFree(LPVOID lpAddress, SIZE_T dwSize, DWORD dwFreeType)",                         "Release/decommit virtual pages"),
    "VirtualProtect": ("BOOL   VirtualProtect(LPVOID lpAddress, SIZE_T dwSize, DWORD flNewProtect, PDWORD lpflOldProtect)", "Change page protection"),
    "VirtualQuery":   ("SIZE_T VirtualQuery(LPCVOID lpAddress, PMEMORY_BASIC_INFORMATION lpBuffer, SIZE_T dwLength)",   "Query virtual memory region info"),

    # ------------------------------------------------------------------ #
    # Windows — file I/O
    # ------------------------------------------------------------------ #
    "CreateFileA":  ("HANDLE CreateFileA(LPCSTR lpFileName, DWORD dwAccess, DWORD dwShare, LPSECURITY_ATTRIBUTES lpSA, DWORD dwCreation, DWORD dwFlags, HANDLE hTemplate)", "Open/create file (ANSI)"),
    "CreateFileW":  ("HANDLE CreateFileW(LPCWSTR lpFileName, DWORD dwAccess, DWORD dwShare, LPSECURITY_ATTRIBUTES lpSA, DWORD dwCreation, DWORD dwFlags, HANDLE hTemplate)", "Open/create file (Unicode)"),
    "ReadFile":     ("BOOL ReadFile(HANDLE hFile, LPVOID lpBuf, DWORD nToRead, LPDWORD lpRead, LPOVERLAPPED lpOv)",     "Read from file or device"),
    "WriteFile":    ("BOOL WriteFile(HANDLE hFile, LPCVOID lpBuf, DWORD nToWrite, LPDWORD lpWritten, LPOVERLAPPED lpOv)", "Write to file or device"),
    "CloseHandle":  ("BOOL CloseHandle(HANDLE hObject)",                            "Close Win32 kernel object handle"),
    "GetFileSize":  ("DWORD GetFileSize(HANDLE hFile, LPDWORD lpFileSizeHigh)",     "Return 32-bit file size (use GetFileSizeEx for large files)"),
    "GetFileSizeEx":("BOOL GetFileSizeEx(HANDLE hFile, PLARGE_INTEGER lpFileSize)", "Return 64-bit file size"),
    "SetFilePointer":("DWORD SetFilePointer(HANDLE hFile, LONG lDistanceToMove, PLONG lpDistanceToMoveHigh, DWORD dwMoveMethod)", "Move file pointer"),
    "FlushFileBuffers":("BOOL FlushFileBuffers(HANDLE hFile)",                      "Flush write buffers to disk"),

    # ------------------------------------------------------------------ #
    # Windows — synchronization
    # ------------------------------------------------------------------ #
    "WaitForSingleObject":   ("DWORD WaitForSingleObject(HANDLE hHandle, DWORD dwMilliseconds)",                                  "Wait for object; returns WAIT_OBJECT_0/TIMEOUT/FAILED"),
    "WaitForMultipleObjects":("DWORD WaitForMultipleObjects(DWORD nCount, const HANDLE* lpHandles, BOOL bWaitAll, DWORD dwMs)",   "Wait for any/all of nCount handles"),
    "CreateMutexA":          ("HANDLE CreateMutexA(LPSECURITY_ATTRIBUTES lpSA, BOOL bInitialOwner, LPCSTR lpName)",               "Create/open named or unnamed mutex"),
    "CreateMutexW":          ("HANDLE CreateMutexW(LPSECURITY_ATTRIBUTES lpSA, BOOL bInitialOwner, LPCWSTR lpName)",              "Create/open named or unnamed mutex (Unicode)"),
    "ReleaseMutex":          ("BOOL   ReleaseMutex(HANDLE hMutex)",                                                               "Release mutex ownership"),
    "CreateEventA":          ("HANDLE CreateEventA(LPSECURITY_ATTRIBUTES lpSA, BOOL bManualReset, BOOL bInitialState, LPCSTR lpName)", "Create/open event object"),
    "SetEvent":              ("BOOL   SetEvent(HANDLE hEvent)",                                                                   "Set event to signaled state"),
    "ResetEvent":            ("BOOL   ResetEvent(HANDLE hEvent)",                                                                 "Set event to non-signaled state"),
    "EnterCriticalSection":  ("void   EnterCriticalSection(LPCRITICAL_SECTION lpCS)",                                            "Acquire critical section (blocks if owned)"),
    "LeaveCriticalSection":  ("void   LeaveCriticalSection(LPCRITICAL_SECTION lpCS)",                                            "Release critical section"),
    "InitializeCriticalSection":("void InitializeCriticalSection(LPCRITICAL_SECTION lpCS)",                                      "Initialize critical section object"),
    "DeleteCriticalSection": ("void   DeleteCriticalSection(LPCRITICAL_SECTION lpCS)",                                           "Free resources of critical section"),
    "TryEnterCriticalSection":("BOOL  TryEnterCriticalSection(LPCRITICAL_SECTION lpCS)",                                         "Try to acquire CS without blocking; TRUE on success"),

    # ------------------------------------------------------------------ #
    # Windows — exception / error
    # ------------------------------------------------------------------ #
    "RaiseException":              ("void RaiseException(DWORD dwCode, DWORD dwFlags, DWORD nArgs, const ULONG_PTR* lpArgs)", "Raise SEH exception"),
    "SetUnhandledExceptionFilter": ("LPTOP_LEVEL_EXCEPTION_FILTER SetUnhandledExceptionFilter(LPTOP_LEVEL_EXCEPTION_FILTER lpFilter)", "Install top-level SEH filter"),
    "UnhandledExceptionFilter":    ("LONG UnhandledExceptionFilter(EXCEPTION_POINTERS* pExcInfo)",                           "Default handler for unhandled exceptions"),
    "terminate":                   ("void terminate(void)",                                                                  "C++ terminate — called on unhandled exception; calls std::terminate_handler"),
    "abort":                       ("void abort(void)",                                                                      "Abnormal termination — raises SIGABRT, no cleanup"),

    # ------------------------------------------------------------------ #
    # Windows — string conversion
    # ------------------------------------------------------------------ #
    "MultiByteToWideChar": ("int MultiByteToWideChar(UINT CodePage, DWORD dwFlags, LPCCH lpMBStr, int cbMB, LPWSTR lpWCStr, int cchWC)", "Convert multibyte to UTF-16"),
    "WideCharToMultiByte": ("int WideCharToMultiByte(UINT CodePage, DWORD dwFlags, LPCWCH lpWCStr, int cchWC, LPSTR lpMBStr, int cbMB, LPCCH lpDefault, LPBOOL lpUsed)", "Convert UTF-16 to multibyte"),
    "lstrlenA":  ("int    lstrlenA(LPCSTR lpString)",                  "Win32 ANSI string length"),
    "lstrlenW":  ("int    lstrlenW(LPCWSTR lpString)",                 "Win32 Unicode string length"),
    "lstrcpyA":  ("LPSTR  lstrcpyA(LPSTR lpDst, LPCSTR lpSrc)",       "Win32 ANSI string copy"),
    "lstrcpyW":  ("LPWSTR lstrcpyW(LPWSTR lpDst, LPCWSTR lpSrc)",     "Win32 Unicode string copy"),
    "lstrcmpiA": ("int    lstrcmpiA(LPCSTR lpStr1, LPCSTR lpStr2)",   "Case-insensitive ANSI string compare"),

    # ------------------------------------------------------------------ #
    # Windows — module / resource
    # ------------------------------------------------------------------ #
    "GetModuleHandleA":  ("HMODULE GetModuleHandleA(LPCSTR lpModuleName)",   "Get handle of loaded module (NULL = calling exe)"),
    "GetModuleHandleW":  ("HMODULE GetModuleHandleW(LPCWSTR lpModuleName)",  "Get handle of loaded module (Unicode)"),
    "GetProcAddress":    ("FARPROC GetProcAddress(HMODULE hModule, LPCSTR lpProcName)", "Get function pointer by name from DLL"),
    "LoadLibraryA":      ("HMODULE LoadLibraryA(LPCSTR lpLibFileName)",      "Load DLL into process (ANSI)"),
    "LoadLibraryW":      ("HMODULE LoadLibraryW(LPCWSTR lpLibFileName)",     "Load DLL into process (Unicode)"),
    "FreeLibrary":       ("BOOL    FreeLibrary(HMODULE hLibModule)",         "Decrement DLL reference count / unload"),
}
