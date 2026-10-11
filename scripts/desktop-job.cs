// The sole job handle stays in the foreground launcher. Windows closes it even
// when the console is closed or the launcher is forcibly terminated.
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class DesktopJob
{
    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimits {
        public long ProcessTime, JobTime;
        public uint Flags;
        public UIntPtr MinWorkingSet, MaxWorkingSet;
        public uint ActiveProcesses;
        public UIntPtr Affinity;
        public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters { public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes; }
    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimits {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct StartupInfo {
        public uint Size;
        public string Reserved, Desktop, Title;
        public uint X, Y, Width, Height, XChars, YChars, Fill, Flags;
        public ushort ShowWindow, ReservedSize;
        public IntPtr ReservedData, Input, Output, Error;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct ProcessInfo { public IntPtr Process, Thread; public uint ProcessId, ThreadId; }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimits limits, uint size);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CreateProcess(string app, StringBuilder command, IntPtr processAttributes,
        IntPtr threadAttributes, bool inheritHandles, uint flags, IntPtr environment,
        string directory, ref StartupInfo startup, out ProcessInfo process);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern uint WaitForMultipleObjects(uint count, IntPtr[] handles, bool all, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, int processId);
    [DllImport("kernel32.dll")]
    static extern IntPtr GetStdHandle(int handle);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetExitCodeProcess(IntPtr process, out uint code);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool TerminateProcess(IntPtr process, uint code);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);

    static void Check(bool success) {
        if (!success) throw new Win32Exception(Marshal.GetLastWin32Error());
    }

    public static int Run(string executable, string commandLine, string directory, int ownerProcessId) {
        return Run(executable, commandLine, directory, ownerProcessId, false);
    }

    public static int Run(string executable, string commandLine, string directory, int ownerProcessId, bool newConsole) {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        ProcessInfo child = new ProcessInfo();
        IntPtr owner = IntPtr.Zero;
        bool assigned = false;
        try {
            var limits = new ExtendedLimits();
            limits.Basic.Flags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
            Check(SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits)));
            // Also cover ending the outer CMD process without a console event.
            owner = OpenProcess(0x100000, false, ownerProcessId); // SYNCHRONIZE
            if (owner == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
            var startup = new StartupInfo();
            startup.Size = (uint)Marshal.SizeOf(startup);
            if (newConsole) {
                startup.Flags = 1; // STARTF_USESHOWWINDOW
                startup.ShowWindow = 1; // SW_SHOWNORMAL
            } else {
                startup.Flags = 0x100; // STARTF_USESTDHANDLES
                startup.Input = GetStdHandle(-10);
                startup.Output = GetStdHandle(-11);
                startup.Error = GetStdHandle(-12);
            }
            // CREATE_SUSPENDED: no child can escape before joining the job.
            // No CREATE_NO_WINDOW/DETACHED_PROCESS; legacy hidden callers get
            // an explicitly new visible console, normal CMD callers share theirs.
            // The job handle itself is non-inheritable.
            Check(CreateProcess(executable, new StringBuilder(commandLine), IntPtr.Zero,
                IntPtr.Zero, true, newConsole ? 0x14u : 4u, IntPtr.Zero, directory, ref startup, out child));
            Check(AssignProcessToJobObject(job, child.Process));
            assigned = true;
            Check(ResumeThread(child.Thread) != uint.MaxValue);
            uint ended = WaitForMultipleObjects(2, new IntPtr[] { child.Process, owner }, false, uint.MaxValue);
            Check(ended == 0 || ended == 1);
            if (ended == 1) return 0;
            uint code;
            Check(GetExitCodeProcess(child.Process, out code));
            return unchecked((int)code);
        } finally {
            if (child.Process != IntPtr.Zero && !assigned) TerminateProcess(child.Process, 1);
            CloseHandle(job);
            if (owner != IntPtr.Zero) CloseHandle(owner);
            if (child.Thread != IntPtr.Zero) CloseHandle(child.Thread);
            if (child.Process != IntPtr.Zero) CloseHandle(child.Process);
        }
    }
}
