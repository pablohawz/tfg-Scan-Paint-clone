import os
import numpy as np

from PySide2.QtCore import QObject, Signal

from ..models.DisplayResultsModel import DisplayResultsModel
from ..models.ActualProjectModel import ActualProjectModel

from ..services import file as fileutils
from ..services.path import interpolate_coords
from ..services import dsp


class DspThread(QObject):
    update_status = Signal(int)
    finished = Signal()

    # def __init__(self, model: DisplayResultsModel, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #     model = model
    #     self.init_values()

    # def init_values(self):
    #     self.trimmed_audio: np.ndarray = None

    @staticmethod
    def log(msg: str) -> None:
        print(f'[DSP Thread] {msg}')

    def process(self, model: DisplayResultsModel):

        self.log('Running!')

        self.trimmed_audio = self.trim_audio(model)
        shift, trim = self.clean_data_position(model)
        model.audio_data = self.trimmed_audio[shift:-trim]

        spatial_segmentation = self.segment_video(model)
        audio_segments = self.segment_audio(model, spatial_segmentation)

        freq, spec = self.analyze(model, audio_segments)
        model.spectrum = spec
        model.freq = freq

        self.finished.emit()

    def trim_audio(self, model) -> np.ndarray:
        audio_len = len(model.audio_data)
        video_len = len(model.data_x)
        fs = model.audio_fs
        fps = model.fps

        if (audio_len/fs < video_len/fps):
            # raise Exception('ERROR: Audio is shorter than needed...')
            self.log('Audio is shorter than needed. ' +
                     'Deleting the last position data')
            diff_in_samples = video_len / fps*fs - audio_len
            self.log(f'diff_in_audio_samples: {int(diff_in_samples)}')

            position_data_to_remove = int(diff_in_samples/fs*fps)+1
            self.log(f'position_data_to_remove: {position_data_to_remove}')

            model.data_x = model.data_x[:-position_data_to_remove]
            model.data_y = model.data_y[:-position_data_to_remove]

        max_audio_len = int(video_len * model.audio_fs / model.fps)

        self.log(f'Trimming the last {abs(audio_len-max_audio_len)} ' +
                 'samples from audio')

        return model.audio_data[:max_audio_len]

    def clean_data_position(self, model) -> tuple:
        if np.isnan(model.data_x).all():
            self.log('ERROR: There is no localization data to analyze')
            raise Exception('...')

        model.data_x, shift, trim = interpolate_coords(
            model.data_x)
        model.data_y, _, _ = interpolate_coords(model.data_y)

        return shift, trim

    def segment_video(self, model) -> dict[tuple, list[tuple]]:
        data = np.transpose(np.array([model.data_x, model.data_y]))

        spatial_segmentation: dict[tuple, list[tuple]] = {}
        for i in range(model.grid.number_of_cols):
            for j in range(model.grid.number_of_rows):
                spatial_segmentation[j, i] = []

        start = 0
        end = 0
        prev_grid_id = -1

        x, y = data[0]
        prev_grid_id = model.grid.locate_point((x, y)).astype(int).tolist()

        for index, point in enumerate(data[1:]):
            x, y = point

            actual_grid_id = model.grid.locate_point((x, y))
            print(index, ' -> ', actual_grid_id)

            # TODO: fix this
            if actual_grid_id is None:
                # If the point was outside of the Grid itself (padding...).
                continue

            # np.array to python list
            actual_grid_id = actual_grid_id.astype(int).tolist()

            if prev_grid_id == actual_grid_id:
                end = index
            else:
                # add element to the dict
                key = (prev_grid_id[0], prev_grid_id[1])
                spatial_segmentation[key].append((start, end))

                # reestart "counter"
                start = index
                end = index
                prev_grid_id = actual_grid_id

        # adding last bit
        key = (actual_grid_id[0], actual_grid_id[1])
        spatial_segmentation[key].append((start, end))

        self.log('Spatial segmentation results: ')
        for grid_id in [*spatial_segmentation]:
            self.log(f' - {grid_id} -> {spatial_segmentation[grid_id]}')

        return spatial_segmentation

    def segment_audio(self, model,
                      segmentation: dict[tuple, list[tuple]]
                      ) -> dict[tuple, list[tuple]]:
        fps = model.fps
        fs = model.audio_fs
        conversion_ratio = fs / fps

        audio_segments: dict[tuple, list[tuple]] = {}
        for grid_id in [*segmentation]:
            audio_segments[grid_id] = []

            for range in segmentation[grid_id]:
                start = int(range[0] * conversion_ratio)
                end = int(range[1] * conversion_ratio)
                audio_segment = model.audio_data[start:end]
                audio_segments[grid_id].extend(audio_segment)

        return audio_segments

    def analyze(self, model, audio_segments):
        cols = model.grid.number_of_cols
        rows = model.grid.number_of_rows

        # Spectrum is of shape = (rows, cols, 0)
        spectrum = [[[] for _ in range(cols)]
                    for _ in range(rows)]
        freq = []
        limits = model.freq_range
        fs = model.audio_fs

        self.log(f'Limits: {limits}')

        for index, key in enumerate([*audio_segments]):
            self.log(f'Processing grid: {key}')
            self.update_status.emit(index)

            audio = np.transpose(audio_segments[key])

            if len(audio) == 0:
                self.log('Len audio == 0. Continuing...')
                continue

            _spl, _freq = dsp.get_spectrum(audio, fs, limits)
            freq = _freq
            spectrum[key[0]][key[1]] = _spl

        self.log(f'Freq array: {freq}')

        self.save(spectrum, freq)

        return np.array(freq), np.array(spectrum, dtype=object)

    def save(self, sp, freq):
        spectrum = []
        for row in sp:
            for col in row:
                spectrum.append(col)

        fileutils.save_np_to_txt(spectrum, os.path.join(
            ActualProjectModel.project_location, 'Results'), 'results.spec')
        fileutils.save_np_to_txt(freq, os.path.join(
            ActualProjectModel.project_location, 'Results'), 'resutls.freq')
