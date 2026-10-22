using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
internal static class Launcher {
    [STAThread]
    private static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string target = Path.Combine(root, @"releases\0.8.2\MediaGrab\MediaGrab.exe");
        bool check = args.Length == 1 && args[0] == "--self-test";
        try {
            if (!File.Exists(target)) throw new FileNotFoundException();
            var start = new ProcessStartInfo(target) {
                WorkingDirectory = Path.GetDirectoryName(target),
                UseShellExecute = false,
                CreateNoWindow = true,
                Arguments = check ? "--self-test" : ""
            };
            using (Process child = Process.Start(start)) {
                if (child == null) return 1;
                if (check) { child.WaitForExit(); return child.ExitCode; }
            }
            return 0;
        } catch {
            if (!check) MessageBox.Show("MediaGrab could not start. Keep this EXE together with the releases folder.", "MediaGrab", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
