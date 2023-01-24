import os
import subprocess
import shutil
from glob import glob

from .fmriprep import fmriprep_cmd, fmriprep_success
from .compression import copy_files_to_lzma_tar
from .resample_workflow import resample_workflow
from .confound import confound_workflow


class PreprocessWorkflow(object):
    def __init__(self, config):
        self.config = config
        sid = self.config['sid']
        self.sid = sid

        for key in ['fmriprep_work', 'fmriprep_out', 'output_root']:
            os.makedirs(self.config[key], exist_ok=True)

        self.log_dir = os.path.join(self.config['output_root'], 'logs')

        fmriprep_dir = os.path.join(self.config['output_root'], 'fmriprep')
        freesurfer_dir = os.path.join(self.config['output_root'], 'freesurfer')
        summary_dir = self.config['output_summary_root']
        confounds_dir = os.path.join(self.config['output_root'], 'confounds')
        self.resample_dir = os.path.join(self.config['output_data_root'], 'resampled')
        self.confound_dir = os.path.join(self.config['output_data_root'], 'confounds')
        for dir_name in [self.log_dir, fmriprep_dir, freesurfer_dir, summary_dir, confounds_dir, self.confound_dir]:
            os.makedirs(dir_name, exist_ok=True)

        self.fmriprep_out = os.path.join(self.config['fmriprep_out'], f'sub-{sid}')
        self.freesurfer_out = os.path.join(self.config['fmriprep_out'], 'sourcedata', 'freesurfer', f'sub-{sid}')
        major, minor = self.config['fmriprep_version'].split('.')[:2]
        if int(major) >= 22:
            self.work_out = os.path.join(config['fmriprep_work'], f'fmriprep_{major}_{minor}_wf', f'single_subject_{sid}_wf')
        else:
            self.work_out = os.path.join(config['fmriprep_work'], f'fmriprep_wf', f'single_subject_{sid}_wf')

        self.fmriprep_fn = os.path.join(fmriprep_dir, f'{sid}.tar.lzma')
        self.freesurfer_fn = os.path.join(freesurfer_dir, f'{sid}.tar.lzma')
        self.summary_fn = os.path.join(summary_dir, f'{sid}.tar.lzma')
        self.confounds_fn = os.path.join(confounds_dir, f'{sid}.tar.lzma')
    
    def _run_method(self, name='fmriprep', **kwargs):
        sid = self.sid
        finish_fn = f'{self.log_dir}/{sid}_{name}_finish.txt'
        running_fn = f'{self.log_dir}/{sid}_{name}_running.txt'
        error_fn = f'{self.log_dir}/{sid}_{name}_error.txt'

        if os.path.exists(finish_fn):
            return True
        if os.path.exists(error_fn):
            return False
        if os.path.exists(running_fn):
            return False
        with open(running_fn, 'w') as f:
            f.write('')

        if name == 'fmriprep':
            step = self._run_fmriprep
        elif name == 'resample':
            step = self._run_resample
        elif name == 'compress':
            step = self._run_compress
        elif name == 'cleanup':
            step = self._run_cleanup
        elif name == 'confound':
            step = self._run_confound
        else:
            raise ValueError
        try:
            success, message = step(**kwargs)
            error = None
        except Exception as e:
            success = False
            error = e
            message = str(e)

        fn = finish_fn if success else error_fn
        with open(fn, 'w') as f:
            f.write(message)
        if os.path.exists(running_fn):
            os.remove(running_fn)

        if error is not None:
            raise error

        if not success:
            print(message)
            exit(1)
        return success

    def _run_fmriprep(self):
        sid = self.sid
        cmd = fmriprep_cmd(self.config)
        stdout_fn = os.path.join(self.log_dir, f'{sid}_fmriprep_stdout.txt')
        stderr_fn = os.path.join(self.log_dir, f'{sid}_fmriprep_stderr.txt')
        with open(stdout_fn, 'w') as f1, open(stderr_fn, 'w') as f2:
            proc = subprocess.run(cmd, stdout=f1, stderr=f2)

        success = fmriprep_success(proc.returncode, stdout_fn, self.fmriprep_out)

        message = '\n'.join([
            f"{self.config['dset']}, {sid}, {proc.returncode}",
            str(self.config), str(cmd), ' '.join(cmd)])
        return success, message

    def _run_resample(self, filter_):
        resample_workflow(
            sid=self.sid, bids_dir=self.config['bids_dir'],
            fs_dir=self.freesurfer_out, wf_root=self.work_out, out_dir=self.resample_dir,
            n_jobs=self.config['n_procs'], combinations=self.config['combinations'], filter_=filter_)
        return True, ''

    def _run_confound(self):
        confound_workflow(self.fmriprep_out, self.confound_dir)
        return True, ''

    def _run_compress(self):
        copy_files_to_lzma_tar(
            self.fmriprep_fn,
            [_ for _ in sorted(glob(os.path.join(self.config['fmriprep_out'], '*'))) if os.path.basename(_) != 'sourcedata'],
            rename_func=lambda x: os.path.relpath(x, self.config['fmriprep_out']),
            exclude = lambda fn: fn.endswith('space-MNI152NLin2009cAsym_res-1_desc-preproc_bold.nii.gz')
        )
        copy_files_to_lzma_tar(
            self.freesurfer_fn,
            [self.freesurfer_out],
            rename_func=lambda x: os.path.relpath(x, os.path.join(self.config['fmriprep_out'], 'sourcedata', 'freesurfer'))
        )
        copy_files_to_lzma_tar(
            self.summary_fn,
            [self.fmriprep_out + '.html'] + sorted(glob(os.path.join(self.fmriprep_out, 'figures', '*'))),
            rename_func=lambda x: os.path.relpath(x, self.config['fmriprep_out']),
        )
        copy_files_to_lzma_tar(
            self.confounds_fn,
            sorted(glob(os.path.join(self.fmriprep_out, 'func', '*.tsv'))) + sorted(glob(os.path.join(self.fmriprep_out, 'ses-*', 'func', '*.tsv'))),
            rename_func=lambda x: os.path.relpath(x, self.config['fmriprep_out']),
        )
        return True, ''

    def _run_cleanup(self):
        if all([os.path.exists(_) for _ in [self.fmriprep_fn, self.freesurfer_fn, self.summary_fn, self.confounds_fn]]):
            for root in [self.config['fmriprep_out'], self.config['fmriprep_work']]:
                if os.path.exists(root):
                    shutil.rmtree(root)
            return True, ''
        else:
            return False, 'Not all output files exist.'

    def fmriprep(self):
        return self._run_method('fmriprep')

    def resample(self, filter_=None):
        return self._run_method('resample', filter_=filter_)

    def compress(self):
        return self._run_method('compress')

    def cleanup(self):
        return self._run_method('cleanup')

    def confound(self):
        return self._run_method('confound')